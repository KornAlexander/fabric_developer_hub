# agenthub-code LLM Response, Progress, Approval, and Collapse Reference

This document describes how `agenthub-code` handles LLM responses and how it informs the user about what is happening during an agent turn. It is written as a Developer Hub implementation reference, not as a product overview. The goal is to capture the mechanics: what events exist, where state lives, what is displayed, how actions and approvals are handled, how steering and queueing work, and how detailed output is later collapsed into a readable transcript.

The key finding is that `agenthub-code` separates live turn feedback from the durable transcript. During a turn it maintains transient state for streaming text, streaming thinking, partial tool-use JSON, in-progress tool IDs, approval queues, prompts, sandbox requests, and spinner mode. When the model or tools produce complete messages, those messages are appended to the canonical `messages` array. The renderer then normalizes, groups, collapses, virtualizes, and renders that canonical history plus selected transient overlays.

## Primary Source Files

Core turn and stream loop:

- `agenthub-code/src/QueryEngine.ts`
- `agenthub-code/src/query.ts`
- `agenthub-code/src/utils/messages.ts`
- `agenthub-code/src/screens/REPL.tsx`
- `agenthub-code/src/hooks/useLogMessages.ts`

Tool execution:

- `agenthub-code/src/services/tools/StreamingToolExecutor.ts`
- `agenthub-code/src/services/tools/toolOrchestration.ts`

Rendering and display transformation:

- `agenthub-code/src/components/Messages.tsx`
- `agenthub-code/src/components/Message.tsx`
- `agenthub-code/src/components/messages/AssistantTextMessage.tsx`
- `agenthub-code/src/components/messages/AssistantThinkingMessage.tsx`
- `agenthub-code/src/components/messages/AssistantToolUseMessage.tsx`
- `agenthub-code/src/components/messages/GroupedToolUseContent.tsx`
- `agenthub-code/src/components/messages/CollapsedReadSearchContent.tsx`
- `agenthub-code/src/components/ToolUseLoader.tsx`
- `agenthub-code/src/components/VirtualMessageList.tsx`

Permission and approval handling:

- `agenthub-code/src/hooks/useCanUseTool.tsx`
- `agenthub-code/src/hooks/toolPermission/handlers/interactiveHandler.ts`
- `agenthub-code/src/components/permissions/PermissionRequest.tsx`

Collapse utilities:

- `agenthub-code/src/utils/collapseReadSearch.ts`
- `agenthub-code/src/utils/collapseBackgroundBashNotifications.ts`
- `agenthub-code/src/utils/collapseHookSummaries.ts`
- `agenthub-code/src/utils/collapseTeammateShutdowns.ts`

Remote and structured protocol handling:

- `agenthub-code/src/cli/structuredIO.ts`
- `agenthub-code/src/server/directConnectManager.ts`
- `agenthub-code/src/hooks/useRemoteSession.ts`

## Mental Model

`agenthub-code` has four layers that cooperate during a turn.

1. Turn orchestration layer

   `QueryEngine.submitMessage` and `queryLoop` decide what context goes to the model, when to compact, when to call tools, and when to continue the loop. This layer emits messages and stream events through an async generator.

2. Live stream state layer

   `REPL.tsx` consumes generator events through `handleMessageFromStream`. It updates transient state like `streamingText`, `streamingThinking`, `streamingToolUses`, `streamMode`, `responseLengthRef`, and `inProgressToolUseIDs`.

3. Canonical transcript layer

   Completed messages are appended to `messages`. The transcript is the durable source of truth. It is also recorded incrementally by `useLogMessages` so resume can reconstruct state.

4. Render transformation layer

   `Messages.tsx` normalizes messages, builds lookup maps, inserts synthetic live tool-use rows, filters progress out of the main visible list, groups related tool calls, collapses low-level activity, and renders either a virtualized list or a capped list.

The important design principle is: the user sees live progress immediately, but the final transcript is semantically compressed after work settles.

## Full Turn Lifecycle

### 1. User Submits Input

The REPL owns prompt input state and query state. The main state involved is:

- `messages`: canonical conversation history.
- `messagesRef`: synchronous ref mirror of `messages`, used because React state commits are asynchronous.
- `inputValue`: current prompt editor content.
- `userInputOnProcessing`: placeholder text displayed while a submitted prompt has not yet appeared in visible history.
- `queryGuard`: synchronous state machine for idle, dispatching, and running. It prevents concurrent local queries.
- `submitCount`: count of submitted prompts.
- `abortController`: current turn cancellation signal.

On submit, the REPL appends the user message to `messages`, resets stream state, resets response metrics, and starts `onQueryImpl`. If a query is already running, `queryGuard.tryStart()` returns `null` and the new user text is not lost. It is enqueued as a prompt command instead.

User-visible behavior:

- The typed prompt is echoed into the transcript as a user message.
- If the real user message has not landed in visible history yet, `userInputOnProcessing` is displayed as a temporary user text placeholder.
- If another query is already running, the new prompt is queued instead of interrupting the current turn.

Developer Hub lesson:

- Keep a synchronous query guard or server-side turn lock. Do not rely only on async UI state to prevent double-submits.
- Show the submitted user input immediately or as a placeholder. The user should never wonder whether their click was accepted.

### 2. QueryEngine Builds Context and Persists the User Message

`QueryEngine.submitMessage` prepares the system prompt, user context, system context, tools, permission wrappers, and command handling. It records user messages before the query loop starts.

Important behavior:

- User messages are recorded before any model response. If the process is killed before the API returns, resume still has the user turn.
- Local commands and compact-boundary messages can be handled before model execution.
- `wrappedCanUseTool` records permission denials so later logic can reason about denied tools.

User-visible behavior:

- The input appears even before the first token from the model.
- Local commands can replace the normal model turn with command UI.
- Compacting can reset the visible context boundary while preserving scrollback in fullscreen mode.

Developer Hub lesson:

- Persist the mission/user request before invoking slow runtime work.
- Emit early durable state. A mission should have visible identity and accepted input before sidecars, tools, or LLM calls attach.

### 3. queryLoop Emits `stream_request_start`

`query.ts` is the main async generator. Each model request iteration begins by yielding:

```ts
{ type: 'stream_request_start' }
```

`handleMessageFromStream` maps this event to:

```ts
onSetStreamMode('requesting')
```

User-visible behavior:

- The spinner changes to a requesting state before any model token arrives.
- The user gets immediate feedback that a model request has started.

Developer Hub lesson:

- Emit an explicit `request_started` or `llm_request_started` event. Do not wait for the first text delta to show progress.

### 4. Model Stream Events Update Transient UI State

The model stream produces `stream_event` messages. `handleMessageFromStream` interprets them without appending every delta to the transcript.

Key stream events:

- `message_start`: captures time-to-first-token metrics when available.
- `content_block_start`: switches mode based on block type.
- `content_block_delta`: updates response length, live text, tool JSON, or thinking metrics.
- `content_block_stop`: no visible state by itself.
- `message_delta`: switches mode back to responding.
- `message_stop`: switches mode to tool-use and clears partial streaming tool-use previews.

Spinner mode mapping:

- `stream_request_start` -> `requesting`
- text block start -> `responding`
- thinking block start -> `thinking`
- tool-use block start -> `tool-input`
- message stop -> `tool-use`

User-visible behavior:

- The spinner verb changes as the turn moves from request, to thinking, to response, to tool input, to tool use.
- Streaming text appears separately from the durable final message.
- Tool input construction can appear before the final assistant message is committed.

Developer Hub lesson:

- Use an explicit phase field, not a single generic `running` state. Users need to know whether the system is asking the LLM, receiving text, preparing tool input, executing tools, or waiting for approval.

## Streaming Text Display

`REPL.tsx` stores streaming text in:

```ts
const [streamingText, setStreamingText] = useState<string | null>(null)
```

`handleMessageFromStream` appends text deltas to this state:

```ts
onStreamingText?.(text => (text ?? '') + deltaText)
```

However, the UI does not show every partial character. It computes:

```ts
const visibleStreamingText = streamingText
  ? streamingText.substring(0, streamingText.lastIndexOf('\n') + 1) || null
  : null
```

This means only complete lines are displayed. The current partial line is hidden until it gets a newline.

Why this matters:

- The user sees smooth line-by-line progress instead of distracting character flicker.
- Markdown rendering has fewer half-formed constructs.
- The final assistant message can replace the streaming preview atomically.

When a completed assistant message arrives, `handleMessageFromStream` clears streaming text before appending the final message:

```ts
onStreamingText?.(() => null)
onMessage(message)
```

The comment in code calls out the goal: switch from deferred messages to real messages in the same batch, avoiding a gap or duplicate text.

Streaming text is disabled when:

- Reduced motion is preferred.
- The terminal has a known cursor-up viewport issue.
- A teammate/agent task view is active instead of the leader view.

User-visible behavior:

- During a model answer, completed lines appear with markdown formatting under the current conversation.
- The spinner is hidden while streaming text is visible, because the streamed text itself is the feedback.
- When the final assistant message lands, the preview disappears and the durable message appears in the same place.

Developer Hub lesson:

- For web UI, stream coherent chunks or complete lines into a temporary render surface. When the final message arrives, replace the temporary surface with the durable message in one state transition.
- Avoid showing both the spinner and the text stream unless the text stream is suppressed.

## Thinking Display

Thinking is treated differently from normal text.

Types:

```ts
export type StreamingThinking = {
  thinking: string
  isStreaming: boolean
  streamingEndedAt?: number
}
```

Important behavior:

- `content_block_start` with `thinking` or `redacted_thinking` sets stream mode to `thinking`.
- `thinking_delta` contributes to response length metrics.
- Completed assistant thinking blocks are captured into `streamingThinking` for real-time transcript display.
- Streaming thinking remains visible while active and for 30 seconds after it ends.
- Past thinking can be hidden in transcript mode when `hidePastThinking` is enabled.

`AssistantThinkingMessage` has two modes:

- Compact mode: shows a concise `Thinking` row with an expand hint.
- Verbose/transcript mode: shows the full thinking markdown.

User-visible behavior:

- Normal mode does not dump long reasoning into the main conversation.
- The UI acknowledges that the model is thinking.
- Full detail is available through verbose/transcript expansion.

Developer Hub lesson:

- Treat internal reasoning or planning detail as a separate channel with visibility rules. Use compact active status by default and expansion for audit/review.

## Streaming Tool Use Construction

Tool-use blocks are streamed as structured content. During `content_block_start` for `tool_use`, `handleMessageFromStream` creates a transient `StreamingToolUse` entry:

```ts
{
  index,
  contentBlock,
  unparsedToolInput: ''
}
```

During `input_json_delta`, it appends `partial_json` into `unparsedToolInput` for the matching block index.

`Messages.tsx` converts transient streaming tool uses into synthetic assistant messages when they are not already present in canonical `messages` and not already executing:

- It filters out streaming tool uses whose IDs are already in `inProgressToolUseIDs`.
- It filters out streaming tool uses whose IDs already exist in normalized durable messages.
- It creates assistant messages with stable derived UUIDs.

User-visible behavior:

- The user can see that the model is preparing a tool/action before that action is finalized.
- Once execution begins or the durable tool-use message arrives, the transient preview is removed.

Developer Hub lesson:

- Represent partial action creation separately from action execution. The UI should be able to say both "preparing action" and "running action".

## Tool Execution While the Model Is Still Streaming

`StreamingToolExecutor` allows tool execution to begin as soon as tool-use blocks arrive, instead of waiting for the entire assistant response to finish.

Tracked tool status values:

- `queued`
- `executing`
- `completed`
- `yielded`

Important methods:

- `addTool(block, assistantMessage)`: parses the tool, determines whether it is concurrency-safe, and queues it.
- `processQueue()`: starts eligible tools while respecting concurrency and exclusivity.
- `executeTool(tool)`: marks the tool in progress, calls `runToolUse`, collects progress, and stores the result.
- `getCompletedResults()`: yields pending progress first, then completed results in order.
- `getRemainingResults()`: waits for remaining tools or progress.
- `markToolUseAsComplete()`: removes IDs from `inProgressToolUseIDs`.

Concurrency behavior:

- Concurrent-safe tools can run together.
- Non-concurrent tools serialize and block other non-concurrent tools.
- Bash failures can abort sibling tools through a shared sibling abort controller.
- User interrupt and streaming fallback produce synthetic tool result errors so every tool use has a matching result.

The non-streaming fallback path is `runTools` in `toolOrchestration.ts`:

- It partitions tool calls into concurrency-safe batches and serial batches.
- Concurrent batches use a max concurrency setting, defaulting to 10.
- Serial batches run one at a time.
- Both paths update `inProgressToolUseIDs`.

User-visible behavior:

- Tool rows can become active while the model stream is still arriving.
- Multiple safe actions can appear active at the same time.
- Long-running shell tools show progress ticks.
- If a sibling shell command fails, other related work is cancelled and represented with synthetic result/error messages.

Developer Hub lesson:

- Do not model all agent progress as a linear log. Use action records with IDs, statuses, concurrency groups, progress events, and terminal results.
- Always close every action with a result, cancellation, or error. Dangling action rows are how UIs get stuck at "Initializing".

## Progress Messages

`createProgressMessage` creates messages with type `progress`. These are canonical messages but are not rendered as ordinary transcript rows.

`Messages.tsx` behavior:

- Progress messages are included when building lookup maps.
- Progress messages are filtered out of the visible normal message list.
- Tool renderers retrieve them through `lookups.progressMessagesByToolUseID`.

Ephemeral progress behavior in `REPL.tsx`:

- Some progress types are ephemeral, such as periodic sleep or shell progress ticks.
- If the new progress tick has the same parent tool-use ID and same data type as the previous progress message, it replaces the previous tick instead of appending.
- Non-ephemeral progress, such as agent progress or hook progress, is preserved because each entry carries distinct state.

User-visible behavior:

- The user sees progress in the owning tool/action row, not as unrelated log spam.
- Repeating ticks update in place.
- Important progress trails remain available.

Developer Hub lesson:

- Separate progress data from log lines. Use progress messages to update action UI in place, and decide per progress type whether history should be appended or replaced.

## Assistant Text Rendering

`AssistantTextMessage` handles final assistant text and assistant error messages.

Normal text behavior:

- Markdown is rendered.
- A marker is shown when `shouldShowDot` is true.
- Empty text returns null.

Error behavior:

- Known error types get specialized user-facing messages.
- Examples include rate limit, prompt too long, low credits, invalid API key, organization disabled, token revoked, timeout, custom off-switch, and user abort.
- Generic API errors are truncated to 1000 characters unless verbose mode is active.
- Truncated errors include an expand hint.

User-visible behavior:

- Users do not see raw stack dumps by default.
- Recoverable/common failures are explained in recognizable language.
- Detail remains available in verbose mode.

Developer Hub lesson:

- Use typed error events and renderers. Do not dump backend exceptions into the primary mission timeline unless no better classification exists.

## Tool Action Rows

`AssistantToolUseMessage` is the main user-visible action renderer.

For each tool-use block it:

- Parses the tool input with the tool input schema.
- Finds the tool definition by name.
- Computes whether the tool is resolved, queued, errored, in progress, or waiting for permission.
- Renders a loader/status dot.
- Renders the bold user-facing tool name.
- Renders tool-specific input summary inside parentheses.
- Renders optional tool-specific tags.
- Renders tool-specific progress or queued messages.
- Hides transparent wrapper tools when appropriate.

Typical action row content:

- Status indicator from `ToolUseLoader`.
- User-facing tool name, not internal tool name.
- Short input description generated by `tool.renderToolUseMessage`.
- Optional progress text from `tool.renderToolUseProgressMessage`.
- Optional queued text from `tool.renderToolUseQueuedMessage`.
- Optional `Waiting for permission...` text if approval is pending.
- Optional classifier checking message for automated Bash permission checks.

`ToolUseLoader` display rules:

- Unresolved active tools have a dim animated dot when animation is enabled.
- Resolved success uses success coloring.
- Errors use error coloring.
- The loader blinks only when active and unresolved.

User-visible behavior:

- A tool is displayed as an understandable action, not as JSON.
- The status indicator changes as the action runs and resolves.
- The row can say it is waiting for permission rather than looking stuck.
- Tool-specific renderers decide what summary matters.

Developer Hub lesson:

- Build action-specific renderers. A file edit, shell command, web fetch, and verification task should not all be displayed as generic log lines.
- Every action row needs: title, summarized input, current state, progress, result/error, and expansion affordance.

## Permission and Approval Flow

Permissions are handled through `useCanUseTool` and the interactive permission handler.

### Permission Decision Entry Point

`useCanUseTool` returns a `CanUseToolFn`. For each tool, it:

1. Creates a permission context.
2. Checks for abort.
3. Uses a forced decision if present, otherwise calls `hasPermissionsToUseTool`.
4. Handles three possible decisions: `allow`, `deny`, or `ask`.

Allow behavior:

- Logs an accept decision.
- Applies updated input if provided.
- Resolves the tool permission promise.

Deny behavior:

- Logs a reject decision.
- Records auto-mode denial when applicable.
- Can show an immediate notification that the tool was denied by auto mode.
- Resolves as denied.

Ask behavior:

- May first allow a coordinator or swarm worker path to resolve.
- May wait briefly for speculative classifier approval for Bash.
- Otherwise calls `handleInteractivePermission`.

### Interactive Permission Handler

`handleInteractivePermission` pushes a `ToolUseConfirm` item into `toolUseConfirmQueue`.

Each queue item includes:

- `assistantMessage`
- `tool`
- `description`
- `input`
- `toolUseContext`
- `toolUseID`
- `permissionResult`
- `permissionPromptStartTimeMs`
- classifier state flags
- optional worker badge
- `onUserInteraction`
- `onAbort`
- `onDismissCheckmark`
- `onAllow`
- `onReject`
- `recheckPermission`

The handler uses a resolve-once guard. Multiple possible responders can race, but only the first winner resolves the permission promise.

Racing responders:

- Local user in the terminal UI.
- Bridge user from remote control or web UI.
- Channel reply from configured messaging channels.
- Permission hooks.
- Bash classifier.
- Coordinator automation.
- Swarm leader/worker permission relay.

Important safeguards:

- User interaction after a short grace period marks `userInteracted` and clears classifier auto-dismiss behavior.
- If the local user responds first, bridge/channel prompts are cancelled or ignored.
- If bridge/channel responds first, the local queue item is removed.
- If a hook decides first, the local/remote prompt is cancelled.
- If classifier allows, the UI can show an auto-approved checkmark transition before removing the prompt.
- Abort removes or resolves pending prompts and logs cancellation.

### PermissionRequest Component

`PermissionRequest` chooses a tool-specific permission component:

- file edit
- file write
- Bash
- PowerShell
- web fetch
- notebook edit
- plan mode
- skill
- ask user question
- workflow
- monitor
- filesystem read/search
- fallback

It also:

- Registers interrupt handling so cancel rejects the prompt.
- Sends a notification if the prompt remains pending.
- Allows specific prompts to register sticky footer UI in fullscreen mode.

User-visible behavior:

- Permission prompts are shown as focused UI, not buried in the log.
- The active session status becomes `waiting`.
- The terminal title/status can indicate what it is waiting for, such as `approve Bash`, `worker request`, `sandbox request`, or `input needed`.
- The normal spinner animation pauses while waiting for approval.
- Pressing interrupt rejects or aborts the prompt.
- Some prompts keep action buttons visible in a sticky footer while the user scrolls long content.

Developer Hub lesson:

- Approvals should be first-class state, not modal side effects. Expose queue length, current approval target, request ID, responder, timeout/notification state, and resolution source.
- Progress should explicitly switch to `waiting_for_approval` with a helpful label.
- Do not let automation dismiss a prompt while the user is actively interacting with it.

## Sandbox, Prompt, Worker, and Elicitation Queues

The REPL has multiple input queues beyond tool permissions:

- `toolUseConfirmQueue`: tool approvals.
- `sandboxPermissionRequestQueue`: local network/domain access prompts.
- `workerSandboxPermissions.queue`: sandbox prompts forwarded by workers.
- `promptQueue`: generic prompt/question requests.
- `elicitation.queue`: MCP elicitation requests.
- `pendingWorkerRequest`: worker waiting for leader approval.
- `pendingSandboxRequest`: worker waiting for sandbox approval.

`getFocusedInputDialog` chooses which dialog is active by priority.

Important priority behavior:

- High-priority message selector wins.
- Permission and interactive dialogs are suppressed while the user is actively typing.
- Sandbox permissions, tool permissions, prompts, worker sandbox permissions, elicitation, cost dialog, idle-return, plan choices, and startup callouts have ordered priority.
- Dialogs are allowed only when tool JSX does not block them or explicitly allows animation to continue.

Typing suppression:

- When the user is typing, interrupt dialogs can be deferred.
- A 1500 ms timeout after the last keystroke clears the active typing flag.
- This avoids accidental keystrokes answering a prompt the user has not read.

User-visible behavior:

- The user gets one focused blocking interaction at a time.
- New prompts are queued rather than stacked.
- Some prompts wait until the user stops typing.
- Worker-side waits show clear pending indicators.

Developer Hub lesson:

- Use a central focus/priority arbiter for all interaction requests. Avoid letting multiple modals, banners, and action cards compete for attention.
- Distinguish passive progress from required user action.

## Interrupt and Cancellation

`onCancel` is the central cancel path.

When the user cancels:

- The query guard is force-ended.
- Proactive mode is paused when applicable.
- If `streamingText` has content, it is appended as a real assistant message before clearing state.
- Loading state and stream state are reset.
- Token budget state is cleared.
- If a tool permission prompt is focused, its `onAbort` is called and the queue is cleared.
- If a generic prompt is focused, all pending prompts are rejected.
- Remote sessions receive a remote interrupt.
- Local sessions abort the current abort controller.
- A turn-complete callback runs with `aborted=true`.

Why preserving partial text matters:

- If the user interrupts mid-answer, the partial generated text remains readable.
- The transcript order becomes: user message, partial assistant message, interruption marker.

Developer Hub lesson:

- Cancellation should preserve useful partial output and then emit a clear interruption event. Do not erase everything the user waited for.
- Cancellation should close every active approval, prompt, and tool action with a terminal state.

## Message Normalization

`normalizeMessages` in `utils/messages.ts` converts API messages into renderable per-block messages.

Important behavior:

- Multi-block assistant messages are split into stable single-block render messages.
- Stable UUIDs are derived from parent UUID plus content index.
- User messages with multiple blocks are similarly normalized.
- Empty messages are filtered by `isNotEmptyMessage`.

Why this exists:

- Each text block, thinking block, tool-use block, and tool-result block can be rendered independently.
- Tool-use rows can be matched to tool-result rows.
- React row keys are stable.
- Virtualization and memoization can work at row granularity.

Developer Hub lesson:

- Normalize model and runtime events into stable item records. Use durable IDs and parent-child relationships. Avoid rendering directly from raw provider chunks.

## Message Lookups

`buildMessageLookups` constructs relationship maps used by renderers.

Examples of lookup data:

- Resolved tool-use IDs.
- Errored tool-use IDs.
- Tool-result by tool-use ID.
- Progress messages by tool-use ID.
- Hook and unresolved hook state.
- Associated messages for grouped/collapsed rows.

User-visible behavior enabled by lookups:

- Tool rows know whether they are resolved or errored.
- Tool rows can show progress messages without displaying progress as separate transcript rows.
- Collapsed groups can show whether anything inside is still running.
- Permission prompts can make the corresponding tool row say `Waiting for permission...`.

Developer Hub lesson:

- Build a derived view model for rendering. Do not make UI components scan raw event arrays independently.

## Main Display Pipeline in Messages.tsx

`Messages.tsx` is the main transformation stage. Its rough pipeline is:

1. Normalize raw messages.
2. Track streaming thinking visibility.
3. Identify the last thinking block.
4. Build synthetic streaming tool-use messages.
5. Apply compact-boundary filtering, depending on mode.
6. Remove progress messages from the main visible list.
7. Hide messages that should not render in the current screen.
8. Apply brief-mode filtering.
9. Apply grouping.
10. Apply collapse utilities.
11. Build message lookups.
12. Apply render cap or virtualized rendering.
13. Render message rows.
14. Render streaming text and streaming thinking overlays.

Key design decision:

- Progress messages are not thrown away. They are removed from ordinary rows after they have been indexed into lookups.

Developer Hub lesson:

- Build timeline rendering as a deterministic pipeline. Each stage should have a clear contract: normalize, index, group, collapse, cap/virtualize, render.

## Grouped Tool Uses

`GroupedToolUseContent` renders tool-specific grouped rows when a tool supports `renderGroupedToolUse`.

It builds `toolUsesData` with:

- `param`: tool-use block.
- `isResolved`: derived from lookups.
- `isError`: derived from lookups.
- `isInProgress`: derived from `inProgressToolUseIDs`.
- `progressMessages`: filtered progress for the tool.
- `result`: parsed tool result data when present.

Then it calls:

```ts
tool.renderGroupedToolUse(toolUsesData, { shouldAnimate, tools })
```

User-visible behavior:

- Repeated related tool calls can appear as one compact UI element.
- Group renderers still know per-item status and progress.

Developer Hub lesson:

- Some repeated actions should be displayed as a batch, but the batch must retain per-child state for expansion and errors.

## Read/Search/Background Work Collapse

The most important collapse behavior is `collapseReadSearchGroups`.

It collapses consecutive low-level operations into semantic summary rows. It does not blindly hide logs. It understands tool classes and message relationships.

Collapsible categories include:

- Search operations.
- File reads.
- Directory listings.
- REPL wrapper and virtual primitive calls.
- Memory reads/searches/writes.
- MCP search/read calls.
- Non-search Bash commands in fullscreen mode.
- Meta operations like snip/tool search, absorbed silently.

Group-breaking messages include:

- Assistant text.
- Non-collapsible tool uses.
- User messages with non-collapsible tool results.

Messages that are skipped but do not break a group:

- Thinking blocks.
- Redacted thinking.
- Some attachments.
- Some system messages.

Special absorbed content:

- PreToolUse hook summaries can be absorbed into the active collapsed group.
- Relevant memory attachments can be absorbed so the summary says memories were recalled.
- Bash results can be scanned for git operations and surface outcomes like committed, pushed, merged, or PR actions.

The collapsed group stores:

- Counts of searches, reads, lists, memory operations, MCP calls, Bash commands.
- Read file paths.
- Search args.
- Latest display hint.
- Original messages for verbose expansion.
- Tool-use IDs.
- Hook timing.
- Git operation metadata.

User-visible active collapsed row:

- Uses present tense: `Searching`, `Reading`, `Listing`, `Running`.
- Shows an animated loader when active.
- Shows an ellipsis while active.
- Shows a short hint row under the summary, such as the current file path, search pattern, command label, MCP query, or inner REPL tool input.
- Holds each hint for at least 700 ms to avoid flicker.
- For long shell commands, after 2 seconds it appends elapsed time and line count.

User-visible completed collapsed row:

- Uses past tense: `Searched`, `Read`, `Listed`, `Ran`.
- Is dimmed.
- Keeps an expand hint.
- May surface load-bearing outcomes like commits or PRs before generic counts.

Verbose behavior:

- In verbose mode, the collapsed group renders every tool use with its one-line result summary.
- Hook details and recalled memories are shown.

Developer Hub lesson:

- Collapse should be semantic and reversible. The active row should reassure the user that work continues; the completed row should summarize what happened and allow expansion.
- Use present tense for active work and past tense for completed work.
- Show the latest meaningful hint, not raw log tail.

## Other Collapse Utilities

`collapseHookSummaries`:

- Collapses consecutive hook summary messages with the same hook label.
- Sums hook counts.
- Concatenates hook infos and errors.
- Preserves flags like prevented continuation and output presence.
- Uses the max duration for parallel hook wall-clock approximation.

`collapseTeammateShutdowns`:

- Collapses consecutive in-process teammate completion attachments.
- Replaces them with a `teammate_shutdown_batch` attachment containing a count.

`collapseBackgroundBashNotifications`:

- Collapses consecutive successful completed background shell task notifications.
- Failed or killed tasks stay individually visible.
- Only runs in fullscreen mode and only outside verbose mode.
- Uses an existing task notification shape so no new renderer is required.

Developer Hub lesson:

- Collapse only noise. Errors, killed tasks, and unusual outcomes should stay visible.
- Prefer synthetic summary items that reuse existing renderers.

## Transcript Mode and Expansion

`agenthub-code` has normal mode and transcript mode.

Normal mode:

- Optimized for current work.
- Collapses low-level read/search/tool chatter.
- Hides most past thinking.
- Shows streaming text and active collapsed groups.

Transcript mode:

- Uses verbose rendering.
- Can show all messages, depending on `showAllInTranscript`.
- Uses virtual scrolling in fullscreen mode.
- Freezes message and streaming tool-use lengths when entering transcript mode so the view does not shift underneath the user.
- Supports search and navigation.

Expansion affordances:

- Collapsed groups include an expand hint.
- API errors include an expand hint when truncated.
- Thinking blocks show compact rows unless expanded/verbose.
- Tool results can be truncated and expanded.

User-visible behavior:

- The default view is readable even during large tool-heavy runs.
- The user can inspect details when needed.
- Entering transcript/search mode does not chase new streaming messages unexpectedly.

Developer Hub lesson:

- Give users a calm current-work view and a separate audit view. Do not make the main mission page carry every raw event at full detail.

## Virtualization and Render Caps

Long sessions are handled through two strategies.

Virtualized fullscreen mode:

- `VirtualMessageList` uses `useVirtualScroll`.
- It maintains stable item keys.
- It incrementally updates key arrays for append-only streams.
- It supports navigation, sticky prompt headers, search, click-to-expand, and measurement.

Non-virtualized mode:

- Uses caps such as `MAX_MESSAGES_WITHOUT_VIRTUALIZATION = 200`.
- Transcript mode has smaller display caps unless virtual scroll is active.
- A UUID anchor avoids scrollback churn when the cap advances.

Performance protections:

- `responseLengthRef` avoids re-rendering the whole REPL on every text delta.
- `Messages` has a memo comparator with special handling for streaming tool uses and streaming thinking.
- `streamingToolUses` can update on every JSON delta, so rendering compares only meaningful content block fields.
- Search text is cached.
- Progress ticks replace previous ephemeral ticks instead of appending forever.

Developer Hub lesson:

- Virtualize long timelines. Also cap fallback rendering.
- Keep per-token counters and metrics in refs or external stores, not global React state that repaints the whole app.

## Terminal Status and Waiting State

The REPL derives a session status:

- `waiting`: permission prompt, local JSX command, prompt, worker request, or sandbox request is active.
- `busy`: model/tool work is active.
- `idle`: no active query or external work.

It also derives `waitingFor`:

- `approve <tool name>`
- `worker request`
- `sandbox request`
- `dialog open`
- `input needed`

This status is written through `updateSessionActivity` for background session visibility and terminal status integration.

User-visible behavior:

- Title/status animation stops when waiting for the user.
- Sleep prevention runs only while active work is ongoing, not while waiting for approval.
- Background session listings can show why a session is blocked.

Developer Hub lesson:

- Make blocked state explicit. A mission should distinguish `running`, `waiting_for_user`, `waiting_for_worker`, `waiting_for_sandbox`, `failed`, and `complete`.

## Remote, SDK, and Direct Connect Protocols

`StructuredIO` provides a structured NDJSON protocol for SDK hosts.

Important control message behavior:

- `control_request` with subtype `can_use_tool` asks the host for permission.
- `control_response` resolves a pending request.
- `control_cancel_request` cancels stale prompts when another responder wins.
- Pending requests are tracked by request ID.
- Resolved tool-use IDs are tracked to ignore late duplicate responses.
- Input stream closure rejects pending permission requests.

SDK permission flow:

- Main permission rules are checked first.
- If a prompt is required, permission hooks and SDK prompt race.
- If a hook decides first, the SDK prompt is aborted.
- If the SDK responds first, hook output is ignored.
- Permission updates can be persisted and applied to local context.

Sandbox network access:

- Uses a synthetic tool name, `SandboxNetworkAccess`, through the same `can_use_tool` control-request protocol.

`DirectConnectSessionManager` behavior:

- Reads NDJSON messages from WebSocket.
- Forwards SDK messages to callbacks.
- Handles `control_request` permission prompts separately.
- Sends `control_response` for permission decisions.
- Sends an interrupt control request for cancellation.
- Filters out protocol messages and streamlined summaries that should not appear as normal chat messages.

Developer Hub lesson:

- Use one structured protocol for user-action requests across local UI, remote UI, and SDK consumers.
- Track request IDs and action IDs. Late duplicate responses must be harmless.

## Error and Recovery Behavior in query.ts

`queryLoop` includes several recovery paths before surfacing an error.

Examples:

- Prompt-too-long recovery.
- Media block removal/recovery.
- Max-output-token recovery.
- Streaming fallback.
- Tombstoning orphaned messages when fallback invalidates earlier streamed content.
- Synthetic tool results on interrupt or fallback so tool-use IDs are balanced.

User-visible behavior:

- Some recoverable errors do not immediately flash as failures.
- If recovery succeeds, the user sees the continued turn.
- If recovery fails, the error is rendered as a typed assistant error message.

Developer Hub lesson:

- Backend recovery should not spam transient internal failures into the main timeline. Emit recovery attempt state when useful, but reserve failure UI for terminal or actionable failure.

## Information Density Strategy

`agenthub-code` uses several tactics to keep information readable:

- Active work is shown in present tense.
- Completed work is shown in past tense.
- Low-level repeated actions are grouped.
- Progress ticks update in place.
- Tool rows show human labels and summaries.
- Long details are behind verbose/transcript mode.
- Errors stay visible and are not collapsed away.
- Approvals interrupt the flow with focused UI.
- The status line/title shows busy vs waiting.
- The transcript is still complete enough for audit because raw details are preserved or expandable.

This avoids the two common bad states:

- Too little detail: user sees only `Initializing` or `Running`.
- Too much detail: user sees every raw log line and cannot understand the current state.

## Concrete Developer Hub Implementation Reference

Developer Hub should borrow these patterns as web-native concepts.

### Event Taxonomy

Use typed events instead of generic log lines. Suggested categories:

- `mission_accepted`
- `llm_request_started`
- `llm_stream_phase_changed`
- `assistant_text_delta`
- `assistant_text_finalized`
- `thinking_started`
- `thinking_delta`
- `thinking_finalized`
- `action_planned`
- `action_input_delta`
- `action_queued`
- `action_started`
- `action_progress`
- `action_result`
- `action_failed`
- `approval_requested`
- `approval_updated`
- `approval_resolved`
- `prompt_requested`
- `prompt_resolved`
- `sandbox_requested`
- `sandbox_resolved`
- `compaction_started`
- `compaction_completed`
- `recovery_started`
- `recovery_succeeded`
- `recovery_failed`
- `mission_interrupted`
- `mission_failed`
- `mission_completed`

Each event should include IDs:

- `missionId`
- `turnId`
- `messageId`
- `actionId`
- `parentActionId`
- `approvalRequestId`
- `source`
- `timestamp`

### State Model

Keep these separate:

- Durable transcript items.
- Live streaming text preview.
- Live thinking preview.
- Partial action input previews.
- Action map by ID.
- Progress map by action ID.
- Approval queue.
- Prompt queue.
- Current focused interaction.
- Mission status and waiting reason.
- Collapse/expansion state.

Do not store everything as a flat list of log strings.

### Active Display Rules

For active work:

- Show phase: requesting, thinking, responding, preparing action, running action, waiting for approval.
- Show only the most useful active hint per group.
- Show elapsed time and output count for long-running shell/process tasks.
- Use present tense.
- Keep the primary action row visible.

### Settled Display Rules

For completed work:

- Collapse repeated reads/searches/listings/background commands.
- Use past tense.
- Keep errors, denies, cancelled tasks, and unusual outcomes visible.
- Preserve expandable detail.
- Surface meaningful outcomes before raw counts.

### Approval UI Rules

Approvals should:

- Be represented as durable request objects.
- Have a visible owner action.
- Make the action row say it is waiting for permission.
- Pause busy animation and mark mission as waiting.
- Support cancel/reject/allow.
- Persist source of resolution: user, rule, hook, classifier, remote, leader, timeout.
- Ignore duplicate or stale responses.
- Avoid auto-dismiss if user has started interacting.

### Collapse Rules

Implement semantic collapse in a deterministic reducer:

- Identify collapsible action types.
- Match action results to action IDs.
- Group consecutive collapsible actions until text or non-collapsible action breaks the group.
- Absorb related hook summaries and memory/context attachments.
- Keep original child items for expansion.
- Render active collapsed groups differently from completed groups.

### Suggested Developer Hub Timeline Item Types

Use view-model item types like:

- `UserMessageItem`
- `AssistantStreamingTextItem`
- `AssistantMessageItem`
- `ThinkingItem`
- `ActionItem`
- `GroupedActionItem`
- `CollapsedActivityGroupItem`
- `ApprovalItem`
- `PromptItem`
- `SystemNoticeItem`
- `ErrorItem`
- `RecoveryItem`
- `MissionOutcomeItem`

Each item should expose:

- `id`
- `kind`
- `status`
- `parentId`
- `startedAt`
- `endedAt`
- `title`
- `summary`
- `details`
- `children`
- `canExpand`
- `severity`

### Avoid These Failure Modes

- Showing only `Initializing` while backend work is actually active.
- Treating failed runtime attach as `complete`.
- Rendering progress ticks as endless log lines.
- Hiding errors inside collapsed groups.
- Showing raw JSON tool input as the main action label.
- Letting approvals appear disconnected from the action that caused them.
- Losing partial streamed text on interrupt.
- Letting duplicate remote approval responses mutate state twice.
- Re-rendering the entire timeline on every token.

## Developer Hub E2E Test Strategy

The Developer Hub frontend should be tested with deterministic event tapes first, and with any LLM review only as an optional diagnostic layer. The product behavior is dynamic, but the test oracle does not need to be nondeterministic: each pushed event stage should have a known visible contract in the DOM, plus a structured evidence snapshot attached to the Playwright report.

The current implementation already has the right hooks for this:

- `Frontend/e2e/mission-control-reference-visual.spec.ts` has a live catch-up pattern where the test mutates a `liveEvents` array and the frontend discovers new events through `/events.json` polling.
- `Frontend/src/components/AgentHub/mission/useMissionStream.ts` can emit `[mc-stream] event` console lines when `localStorage.agenthub_debug_stream` is enabled.
- `Frontend/tests/mission/missionReducer.test.ts` covers deterministic event-to-state behavior.
- `Frontend/tests/mission/executionStream.test.ts` covers deterministic state-to-row behavior.
- `MissionControlPage.tsx` exposes stable browser selectors: `aria-label="Mission execution"`, `aria-label="Active agent execution"`, `role="log" aria-label="Mission log stream"`, `aria-label="Mission receipts"`, `aria-label="Mission steering"`, `.mc3-exec-row--live`, `.mc3-exec-current[data-spinner-mode]`, `.mc3-agent-progress-line`, and `[aria-label="Current task details"]`.

### What The User Should See During Execution

When a mission is executing, the first screen should make the runtime state obvious without asking the user to inspect raw logs:

1. The mission is accepted immediately. The execution surface is visible, the brief repeats the user goal, and the page is not blank while the stream attaches.
2. The header status and connection chip show a real lifecycle: starting, streaming, reconnecting, waiting, failed, or completed. A quiet or reconnecting stream must not be presented as successful completion.
3. Active agent lanes show the Generalist and any spawned specialists. Running, waiting, done, and failed states are visually distinct.
4. The live transcript has one focused current-work row for the active agent. It should show the phase in plain language, a spinner mode, a progress line, and current task details.
5. Model/request phases should be visible as requesting, thinking, responding, preparing tool input, or running tool work. In the current event contract this is approximated by `slot_progress`, `agent_status`, and high-level `log_line` events. When first-class LLM events are added, the same test should assert those explicit phases instead.
6. Tool calls update in place. `tool_call_started`, `tool_progress`, and `tool_call_ended` should not create a noisy wall of equivalent rows.
7. Completed bursts collapse into receipt rows. The user should see what completed, how much detail was hidden, and a way to expand details.
8. Approvals make the mission visibly waiting. The approval should appear both near the live log and in the receipts/attention area, tied to the action that caused it.
9. Steering has public queue semantics. A sent message should be visible as queued, interrupted/deferred when applicable, and delivered when consumed by the agent.
10. Errors and verifier failures stay visible. They must not be swallowed by collapsed groups or hidden behind an optimistic completed state.
11. Terminal completion settles the UI. Live rows stop animating, receipts remain, the final outcome appears, and steering controls stop implying that the mission is still mutable.

### Recommended Playwright Spec

Add a focused contract spec, for example `Frontend/e2e/mission-control-progress-contract.spec.ts`. Keep broad layout checks in the existing redesign/reference specs; this new spec should prove the behavior contract stage by stage.

The spec should use a scripted tape of public mission events and push them over time:

1. Start with an empty `liveEvents` array and mocked session snapshot returning a running session.
2. Navigate to Mission Control and assert the accepted/attaching state.
3. Push `run_overview`, `mission_seeded`, and `generalist_check_in`; assert that the mission plan and Generalist lane become visible.
4. Push `slot_progress` and `agent_status` for a model/request phase; assert a live row appears with a non-idle spinner mode and useful current-task text.
5. Push `generalist_context_pack`, `subagent_spawned`, and specialist `slot_progress`; assert the specialist lane appears and the active row changes owner.
6. Push `tool_call_started` and multiple `tool_progress` events; assert the progress line updates in place and the row count does not grow one row per tick.
7. Push `activity_rollup`; assert a receipt row appears with hidden detail count and expandable children.
8. Push `approval_required` plus waiting `slot_progress`; assert the header, lane, live log, and receipt/attention area all indicate waiting for approval.
9. Push steering events: `user_message_queued`, `turn_interrupt_requested`, `turn_interrupt_deferred`, and `user_message_delivered`; assert the transcript tells the user exactly what happened.
10. Push `approval.resolved`, more tool progress, `change_recorded`, `artifact_added`, and `verifier_verdict`; assert changes/receipts/evidence are visible and failures stay visible.
11. Push `mission_completed` and `job_complete`; assert the page is terminal, no live row is still animating, and final receipts remain available.

This should be a browser-level contract, not a screenshot-only test. Screenshots are useful attachments, but the pass/fail should be based on structured DOM evidence.

### Evidence Helper

Each stage should call a helper that returns a small JSON object describing what the user can currently see. Attach that JSON to the Playwright report so reviewers can inspect the exact in-progress frontend state after a failure.

Example shape:

```ts
async function readMissionEvidence(page: Page, label: string) {
   return page.evaluate((stageLabel) => {
      const text = (selector: string) => document.querySelector(selector)?.textContent?.replace(/\s+/g, " ").trim() ?? "";
      const attr = (selector: string, name: string) => document.querySelector(selector)?.getAttribute(name) ?? null;
      const rows = Array.from(document.querySelectorAll(".mc3-transcript-row")).map((node) => ({
         kind: node.getAttribute("data-kind"),
         state: node.getAttribute("data-state"),
         live: node.classList.contains("mc3-exec-row--live"),
         attention: node.classList.contains("mc3-exec-row--attention"),
         text: node.textContent?.replace(/\s+/g, " ").trim().slice(0, 500) ?? "",
      }));
      return {
         stage: stageLabel,
         execution: text('[aria-label="Mission execution"]'),
         status: text(".mc3-execution-status"),
         statusState: attr(".mc3-execution-status", "data-state"),
         connection: text(".mc3-terminal-connection"),
         connectionState: attr(".mc3-terminal-connection", "data-state"),
         lanes: text('[aria-label="Active agent execution"]'),
         liveRowCount: document.querySelectorAll(".mc3-exec-row--live").length,
         spinnerModes: Array.from(document.querySelectorAll(".mc3-exec-current")).map((node) => node.getAttribute("data-spinner-mode")),
         progressLine: text(".mc3-agent-progress-line"),
         currentTaskDetails: text('[aria-label="Current task details"]'),
         logStream: text('[aria-label="Mission log stream"]'),
         receipts: text('[aria-label="Mission receipts"]'),
         steering: text('[aria-label="Mission steering"]'),
         rows,
      };
   }, label);
}
```

The test should then attach both evidence and a screenshot:

```ts
const evidence = await readMissionEvidence(page, "tool-progress-running");
await testInfo.attach("tool-progress-running.json", {
   body: JSON.stringify(evidence, null, 2),
   contentType: "application/json",
});
await testInfo.attach("tool-progress-running.png", {
   body: await page.screenshot({ fullPage: true }),
   contentType: "image/png",
});
```

### Console Evidence

The test should also capture frontend stream logs. In auth seeding, set:

```ts
await page.addInitScript(() => {
   localStorage.setItem("agenthub_debug_stream", "1");
});
```

Then collect console messages:

```ts
const consoleEvidence: string[] = [];
page.on("console", (message) => {
   const text = message.text();
   if (text.includes("[mc-stream]") || message.type() === "error" || message.type() === "warning") {
      consoleEvidence.push(`${message.type()}: ${text}`);
   }
});
```

Assertions should verify that the frontend observed the event sequence expected for each stage, and that no console error explains a silent UI failure. This is how the test can "see the frontend logs in progress" without asking a model to guess from a screenshot.

### Deterministic Judgement Rules

Use explicit assertions as the CI gate. Useful invariants:

- The execution surface is never blank after mission acceptance.
- `connectionState` matches the stage: streaming while active, waiting when approval is pending, terminal after completion.
- There is at most one focused live row for the active agent at any point in the scripted tape.
- `spinnerModes` contains the expected phase for the stage, such as `requesting`, `thinking`, `responding`, `tool-input`, or `tool-use` when those events are available.
- Tool progress updates replace the current task/progress line instead of appending unbounded duplicate rows.
- An `activity_rollup` creates a receipt with the expected summary and hidden detail count.
- Approval stages show waiting state in the header/lane/log/receipt evidence simultaneously.
- Steering stages show queued, deferred/interrupted, and delivered semantics in user-readable text.
- Terminal stages have no live animated row and do have durable receipts/outcome text.
- Raw implementation tokens are absent from user-facing evidence: internal tool IDs, raw JSON, `TOOL_ERROR`, `undefined`, arrows, stack traces, bearer tokens, and private trace categories.

These rules are stronger than a screenshot comparison because they judge the same things a user cares about: current status, who is working, what is blocked, what changed, and what evidence remains.

### Unit Contract Before Browser Contract

Before adding browser assertions for a new event kind, add or update unit tests:

- `missionReducer.test.ts`: event tape becomes durable `MissionState` and sanitized `LogEntry` objects.
- `executionStream.test.ts`: state/logs become live rows, receipt rows, hidden details, spinner modes, and attention rows.
- `logPresentation.test.ts`: internal runtime text is converted into user-readable text.
- `logVisibility.test.ts`: errors, approvals, steering, and receipts remain visible in all relevant log modes.

The Playwright spec should then prove that React renders those contracts correctly while the event stream is changing.

### Required LLM Judge Test Option

An LLM judge must also be implemented as a secondary test option. It should not replace deterministic Playwright assertions as the normal CI gate, but it must exist, be runnable, and review the same staged evidence that the deterministic contract spec captures.

The distinction is important:

- Required to implement: the repo should contain an LLM judge spec/helper that can be run by developers and nightly jobs.
- Optional to run by default: the judge should be guarded by an environment variable because it depends on GitHub auth, network availability, Copilot model behavior, token limits, and prompt stability.
- Advisory for merge confidence: the judge can catch confusing UX, poor hierarchy, misleading progress language, and screenshots/evidence that technically pass but feel wrong.
- Authoritative inside its own opt-in job: when `AGENTHUB_E2E_LLM_JUDGE=1` is enabled, a blocking judge verdict should fail that optional job unless a separate soft mode is intentionally set.

Recommended file shape:

- `Frontend/e2e/mission-control-progress-contract.spec.ts`: deterministic staged tape and hard DOM assertions.
- `Frontend/e2e/mission-control-llm-judge.spec.ts`: opt-in LLM review over the captured evidence tape.
- `Frontend/e2e/utils/missionEvidence.ts`: shared `readMissionEvidence`, console capture, event tape driver, redaction, and attachment helpers.
- `Frontend/e2e/utils/llmJudge.ts`: Copilot call, JSON parsing, schema validation, and verdict formatting.
- `Frontend/e2e/utils/githubCopilotToken.ts`: token resolver that validates environment, cache, Developer Hub browser localStorage, and GitHub CLI tokens against the backend Copilot exchange before the judge request.
- `Frontend/scripts/e2e-copilot-token.mjs`: local cache helper for an existing Developer Hub browser token or a one-time Copilot GitHub App device-flow token, stored outside the repo with `0600` permissions.

The LLM judge should call the existing Developer Hub backend proxy instead of calling Copilot directly from the browser page. Use `POST /api/github/chat/completions` with the user's GitHub token in the `Authorization` header, `stream: false`, a low temperature, and a compact model such as `gpt-4o-mini` unless the environment overrides it.

Use `AGENTHUB_GITHUB_TOKEN` or `GITHUB_TOKEN` only when the token can be exchanged by the backend at `copilot_internal/v2/token`. A normal `gh auth token` value can be a valid GitHub CLI token and still fail that Copilot exchange with `404`, because it was not minted for the Copilot GitHub App. For repeatable local runs, developers should run `npm run token:e2e` once after signing in through the Developer Hub app or GitHub device flow; this stores a backend-compatible token at `~/.config/agenthub/e2e-copilot-token.json` and future judge runs reuse it after validation. The helper and resolver also check the documented persistent Chromium profile (`PLAYWRIGHT_USER_DATA_DIR` or `~/.config/chromium-wsl`) for Developer Hub's own `github_token`, without printing token values.

Example Playwright shape:

```ts
test.describe("mission control LLM judge", () => {
   test.skip(process.env.AGENTHUB_E2E_LLM_JUDGE !== "1", "Set AGENTHUB_E2E_LLM_JUDGE=1 to run the Copilot-backed judge");

   test("reviews progressive mission evidence", async ({ page, request }, testInfo) => {
      const evidenceTape = await runMissionProgressEvidenceTape(page, testInfo);
      const backendUrl = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";
      const githubToken = await resolveGitHubCopilotToken(request, backendUrl);
      const verdict = await judgeMissionProgressEvidence(request, evidenceTape, {
         backendUrl,
         githubToken: githubToken.token,
         model: process.env.AGENTHUB_E2E_LLM_JUDGE_MODEL || "gpt-4o-mini",
      });

      await testInfo.attach("llm-judge-verdict.json", {
         body: JSON.stringify(verdict, null, 2),
         contentType: "application/json",
      });

      if (process.env.AGENTHUB_E2E_LLM_JUDGE_SOFT !== "1") {
         expect(verdict.pass, verdict.blockingIssues.map((issue) => issue.issue).join("\n")).toBe(true);
      }
   });
});
```

The judge helper should redact secrets and send only the event tape, structured DOM evidence, console stream evidence, and short screenshot metadata. Screenshots should still be attached to the Playwright report for humans. They should be sent to the LLM only if the backend proxy supports multimodal inputs for the chosen model; the baseline judge should work from structured evidence alone.

Example judge request shape:

```ts
async function judgeMissionProgressEvidence(request: APIRequestContext, evidenceTape: MissionEvidenceStage[], options: JudgeOptions) {
   if (!options.githubToken) throw new Error("A backend-compatible GitHub token is required for the LLM judge");

   const response = await request.post(`${options.backendUrl}/api/github/chat/completions`, {
      headers: { Authorization: `Bearer ${options.githubToken}` },
      data: {
         model: options.model,
         stream: false,
         temperature: 0,
         max_tokens: 1200,
         messages: [
            { role: "system", content: MISSION_PROGRESS_JUDGE_SYSTEM_PROMPT },
            { role: "user", content: JSON.stringify({ rubric: MISSION_PROGRESS_RUBRIC, evidenceTape }, null, 2) },
         ],
      },
   });
   if (!response.ok()) throw new Error(`LLM judge request failed: ${response.status()} ${await response.text()}`);
   const body = await response.json();
   return parseJudgeVerdict(body.choices?.[0]?.message?.content || "");
}
```

The judge prompt must be strict. It should ask for JSON only and tell the model to judge whether a real user could understand the mission while it was running, not whether the code looks plausible.

Required verdict schema:

```json
{
   "pass": true,
   "blockingIssues": [
      {
         "stage": "approval-required",
         "issue": "The header says streaming even though the mission is waiting for user approval.",
         "expected": "Header, lane, log, and receipts should all indicate waiting/approval.",
         "evidence": "connectionState=streaming, receipts include Approval required"
      }
   ],
   "concerns": [
      "Tool progress is technically visible but too verbose for the primary stream."
   ],
   "stageFindings": [
      {
         "stage": "tool-progress-running",
         "understandable": true,
         "statusClear": true,
         "activeAgentClear": true,
         "progressClear": true,
         "misleadingCompletion": false,
         "rawInternalTextVisible": false
      }
   ]
}
```

Required rubric checks:

- Can the user tell that the mission was accepted and is active before detailed logs arrive?
- Can the user identify the active agent and current phase at each stage?
- Does progress change in place instead of becoming a noisy wall of rows?
- Are approvals unmistakably waiting for the user, and are they connected to the action that caused them?
- Are steering events understandable as queued, deferred/interrupted, or delivered?
- Are rollups and receipts useful without hiding important errors?
- Are failures and verifier rejections visible enough to prevent a false sense of completion?
- Is terminal completion clearly settled, with no leftover live animation?
- Are raw tool identifiers, JSON blobs, `undefined`, stack traces, bearer tokens, private trace categories, or implementation arrows visible to the user?
- Would a user understand what happened if they only looked at the frontend evidence from each stage?

The LLM judge should run after the deterministic evidence tape has already been collected. It should never be the only test that drives the frontend. The hard Playwright assertions protect against regressions with reproducible checks; the LLM judge adds a required qualitative review path for the difficult question: "does this progressing frontend make sense to a human while the mission is running?"

## Summary of What the User Sees

During a healthy turn, the user experiences this sequence:

1. Their prompt appears immediately.
2. A requesting spinner/status appears.
3. If the model thinks, a compact thinking indicator appears.
4. Text streams line-by-line when available.
5. If the model prepares tools, action rows or collapsed active groups appear.
6. If an action needs approval, the mission changes to waiting and a focused approval prompt appears.
7. While tools run, action rows show progress or grouped summaries update in place.
8. Long low-level activity is compressed into active summary rows with live hints.
9. When actions complete, summaries switch to past tense and dim.
10. The final assistant answer appears as durable markdown.
11. The transcript remains expandable for details.

This is the reference behavior Developer Hub should target: immediate acknowledgement, explicit phase changes, action-centric progress, first-class approvals, semantic collapse, and reversible detail.