# Claude Code Repository Best Practices and Fabric Developer Hub Gap Analysis

## Purpose

This document is a second analysis pass after the interaction-streaming report in `Developer Hub/docs/claude-code-interaction-streaming-analysis.md`.

The earlier report focused on how Claude Code streams what is happening, lets the user steer ongoing work, queues messages, and collapses verbose activity into readable progress. This report looks more broadly across the Claude Code repository and asks a different question:

> What operational, security, persistence, diagnostic, and extensibility practices does Claude Code use that Fabric Developer Hub already matches, partially matches, or should adopt next?

The goal is not to clone Claude Code mechanically. Developer Hub is a web workload with a FastAPI backend, Fabric identity, per-mission MCP runtime containers, and a Mission Control UI. Claude Code is a local terminal application with an Ink UI and a different trust model. The useful exercise is to extract durable engineering patterns and translate them into AgentHub-shaped recommendations.

## Methodology

The analysis used a broad source-tree sweep of `claude-code/src/**`, followed by targeted reads in areas that influence reliability and safety:

- `claude-code/src/Tool.ts`
- `claude-code/src/hooks/useCanUseTool.tsx`
- `claude-code/src/tools/BashTool/BashTool.tsx`
- `claude-code/src/services/mcpServerApproval.tsx`
- `claude-code/src/services/mcp/client.ts`
- `claude-code/src/services/diagnosticTracking.ts`
- `claude-code/src/services/analytics/growthbook.ts`
- `claude-code/src/memdir/memdir.ts`
- `claude-code/src/plugins/builtinPlugins.ts`
- `claude-code/src/utils/permissions/denialTracking.ts`
- `claude-code/src/utils/sessionStorage.ts`
- `claude-code/src/utils/toolResultStorage.ts`
- `claude-code/src/utils/cleanupRegistry.ts`
- `claude-code/src/hooks/useSessionBackgrounding.ts`
- `claude-code/src/tools/TodoWriteTool/TodoWriteTool.ts`

It then compared those patterns against Developer Hub's current AgentHub implementation:

- `Developer Hub/Backend/src/services/agenthub/tool_runtime.py`
- `Developer Hub/Backend/src/services/agenthub/capability_registry.py`
- `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`
- `Developer Hub/Backend/src/services/mcp/mcp_client_manager.py`
- `Developer Hub/Frontend/src/components/AgentHub/mission/useMissionStream.ts`
- `Developer Hub/Frontend/src/components/AgentHub/mission/missionReducer.ts`
- `Developer Hub/Frontend/src/components/AgentHub/mission/logVisibility.ts`
- `Developer Hub/Frontend/src/components/AgentHub/approvals/ApprovalCard.tsx`

## Executive Summary

Developer Hub is already strong in several areas that matter most for a hosted multi-agent workload:

- A single tool-dispatch boundary with policy, kill switches, identity scrubbing, workspace pinning, circuit breaking, and untrusted-output fences.
- A structured event ledger with public event sequence numbers, digests, summaries, trace separation, replay rings, SQLite persistence, and Mission Control reducer idempotence.
- Startup capability validation so catalog drift fails early rather than becoming silent runtime tool failures.
- Per-request MCP process isolation and environment allowlisting.
- Mission-level approval cards with blast radius, reversibility, recovery actions, and tool-call previews.

The biggest gaps are not the basics. They are the higher-order UX and operations practices that make long-running agent work feel trustworthy over hours or across sessions:

1. Add a project MCP server approval workflow before user- or workspace-configured MCP servers are connected.
2. Add diagnostic baseline tracking for Fabric item and definition edits, so Mission Control can distinguish pre-existing issues from agent-introduced issues.
3. Add scoped memory for user, session, and repo/workspace facts, especially naming conventions, Fabric workspace topology, and repeated verification lessons.
4. Add cache-then-refresh runtime configuration and feature flags for long missions.
5. Add stronger token refresh and MCP session expiration recovery for long-lived streams and remote MCP sessions.
6. Add denial tracking and approval-loop damping once write/destructive approval gates become interactive.
7. Add optional plugin/capability toggles to roll out experimental AgentHub abilities without making the catalog all-or-nothing.

## Practices Developer Hub Already Does Well

### 1. Single Tool Runtime Chokepoint

Claude Code has a central `ToolUseContext` and permission path. `Tool.ts` defines the shared execution contract: tools receive common runtime options, abort controllers, permission context, MCP connections, UI callbacks, app state, message history, progress callbacks, and file/memory state.

Developer Hub has the right hosted equivalent in `tool_runtime.py`. Its module docstring is unusually clear and the implementation follows through:

- Every LLM-driven tool call should route through `execute()`.
- `CallerContext` is built from verified Fabric JWT claims, not model output.
- LLM-supplied identity arguments are dropped.
- Tool policies are deny-by-default.
- Global, tenant, and per-tool kill switches are checked at every call.
- Repeated identical calls trigger a circuit breaker.
- Tool output is wrapped in untrusted-content fences before being returned to the model.

Recommendation: keep this as the non-negotiable AgentHub boundary. As new interactive approvals or plugin tools are added, they should integrate into this chokepoint rather than bypass it.

### 2. Capability Validation at Startup

Claude Code invests heavily in making the live tool surface explicit. Tools are typed, MCP tools are discovered, tool names are normalized, and pending MCP servers are visible.

Developer Hub already has an excellent version of this in `capability_registry.py`. It validates:

- skill to tool references,
- agent to skill references,
- discovered tools bound to agent skills,
- unavailable MCP servers that would otherwise cause missing-tool fan-out.

Recommendation: maintain fail-fast validation. Any future plugin registry should feed the same validator, not create a second capability path.

### 3. Structured Mission Event Ledger

Claude Code separates user-facing events, diagnostics, progress, transcript storage, and internal analytics. It treats progress ticks as ephemeral and conversation messages as durable.

Developer Hub's `_JobExecution.emit()` provides a strong web-native equivalent:

- public `seq` for replay and dedupe,
- trace-only events separate from public events,
- `eventId`, `payloadDigest`, `payloadSummary`, and bounded previews,
- ring replay for reconnects,
- SQLite persistence for session reloads,
- JSONL support ledger,
- Mission Control reducer dedupe by `seq`.

Recommendation: this is a core advantage of Developer Hub. Future roll-ups, approvals, diagnostics, and memory events should continue to use this event vocabulary rather than side-channel UI state.

### 4. Tool Output Safety

Claude Code has multiple output safety controls: large-result persistence, MCP content truncation, image downsampling, binary output storage, and UI collapse of read/search commands.

Developer Hub already has two critical safety controls:

- untrusted-output fences in `tool_runtime.py`,
- hard output caps through `MAX_TOOL_OUTPUT_CHARS`.

Recommendation: add persisted large-result references later, but do not weaken the current fences. If large output is persisted, the model should receive a bounded preview plus an explicit artifact reference, not raw unbounded output.

### 5. MCP Runtime Isolation and Environment Hygiene

Claude Code is careful about MCP transport, auth, timeouts, content size, and cleanup. Developer Hub's `mcp_client_manager.py` is already strong in a hosted-server way:

- per-request server process spawning,
- startup discovery,
- local server path validation,
- concurrent discovery with deterministic merge,
- environment allowlist for subprocesses,
- workspace argument validation,
- path traversal checks,
- mutation blocking for application/service-principal tokens.

Recommendation: keep using per-request isolation for first-party MCP tools. If persistent remote MCP sessions are introduced, add the session-expiration recovery described later.

## High-Priority Gaps to Adopt

### 1. Project MCP Server Approval Workflow

Claude Code treats project MCP configuration as a trust boundary. `mcpServerApproval.tsx` reads project-scope MCP config, finds servers whose status is `pending`, and blocks startup on a single-server or multiselect approval dialog. Other MCP code then only connects approved project servers.

Developer Hub currently has a trusted backend-owned MCP configuration. That is reasonable for the current implementation. The gap appears as soon as users, teams, workspaces, plugins, or Fabric items can introduce MCP servers or tool bundles dynamically.

Why it matters:

- MCP servers can expose arbitrary tools.
- A project-configured server can become a remote code, network, or data exfiltration boundary.
- Users need to see what they are approving before an agent can call those tools.

Recommended AgentHub design:

- Add an `mcp_server_status` store with states `pending`, `approved`, `rejected`, `disabled`, and `revoked`.
- Record the source: built-in, admin-installed, workspace-installed, user-installed, plugin-installed, or session-proposed.
- Block discovery and tool exposure for `pending` project/user/plugin servers.
- Emit public events such as `mcp_server_approval_required`, `mcp_server_approved`, and `mcp_server_rejected`.
- Surface the approval in Mission Control using the existing approval-card language: source, server command or URL, declared tools, auth requirements, network scope, workspace scope, and reversibility.
- Include a tenant/admin policy override for organizations that want to disallow user-installed MCP servers entirely.

Priority: high for any dynamic MCP onboarding. If MCP remains backend-owned and deployed by operators only, this can wait.

### 2. Diagnostic Baseline Tracking for Fabric Definition Edits

Claude Code's `diagnosticTracking.ts` captures diagnostics before a file edit and compares them after the edit. It normalizes file URIs, handles special right-side virtual files, opens files so language services are ready, computes new diagnostics only, and formats a bounded summary.

Developer Hub currently has rich mission events and verifier loops, but it does not appear to have a generic baseline-diff diagnostic service for Fabric definition authoring.

Why it matters:

- Fabric items can have many pre-existing warnings or model issues.
- Users need to know whether the agent introduced a problem or merely encountered an existing one.
- Without baselines, agents can waste turns chasing old errors or incorrectly claim responsibility for unrelated issues.

Recommended AgentHub design:

- Before a tool writes or publishes a Fabric definition, capture baseline diagnostics for the target item.
- After the write, run the relevant diagnostic provider: semantic model validation, report definition validation, notebook parse/check, PBIR/PBIP schema validation, SQL endpoint validation, or Fabric API error normalization.
- Store diagnostics as structured records keyed by item id, part path, severity, code, source, and normalized location.
- Emit `diagnostic_baseline_captured`, `diagnostic_new_issues`, and `diagnostic_resolved_issues` events.
- In Mission Control, show new issues by default and place pre-existing issues behind a diagnostic expansion.
- Feed new diagnostics back into the verifier loop as evidence.

Priority: high. This is one of the strongest quality upgrades for Fabric item authoring.

### 3. Approval Loop Damping and Denial Tracking

Claude Code has a tiny but valuable `denialTracking.ts`: consecutive denials and total denials are tracked, and repeated denials trigger fallback to prompting instead of repeatedly invoking the same classifier path.

Developer Hub has approval cards, but as write/destructive tools become more interactive there should be an explicit loop-damping policy.

Why it matters:

- Agents can get stuck repeatedly asking for the same declined action.
- Classifiers or automated policy checks can repeatedly deny similar calls without producing a user-useful alternative.
- Long missions need graceful fallback language: "this path is blocked; choose another route."

Recommended AgentHub design:

- Track approval denials by session, agent, tool, target item, and normalized argument hash.
- After 3 consecutive denials for the same class of action, require the agent to propose an alternative instead of re-asking.
- After a larger total threshold, block further attempts until the user explicitly resets the approval scope.
- Emit `approval_repeated_denial` and `approval_fallback_required` events.
- Include denial history in the agent's next tool policy result so the model adapts.

Priority: high once interactive write approvals are common; medium before then.

## Medium-Priority Gaps to Adopt

### 4. Hierarchical Memory Scopes

Claude Code's memory system is file-backed and scoped. It distinguishes long-lived user facts, session-scoped working notes, and repo/workspace knowledge. It also caps loaded entrypoint content by line count and byte size, which protects prompt size.

Developer Hub currently persists sessions well, but it does not have a dedicated memory taxonomy for reusable AgentHub knowledge.

Why it matters:

- Fabric work is full of recurring workspace-specific conventions: item naming, lakehouse naming, capacity caveats, git branch conventions, tenant rules, certification requirements, and known Fabric quirks.
- Session transcripts are too noisy to serve as durable memory.
- A structured memory layer can reduce repeated discovery and make agents more consistent across missions.

Recommended AgentHub design:

- `user` memory: personal preferences and collaboration style.
- `workspace` memory: Fabric workspace conventions, capacity defaults, naming rules, reusable artifact relationships.
- `repo` memory: codebase and PBIP/PBIR conventions when a Git repo is attached.
- `session` memory: temporary plan state, verifier notes, and decisions that should not outlive the mission.
- Keep a small always-loaded index and load detail on demand.
- Store provenance: who wrote it, when, from which mission, and whether the user confirmed it.
- Add memory events to the ledger: `memory_loaded`, `memory_written`, `memory_updated`, `memory_ignored`.

Priority: medium. It becomes high for repeated enterprise workflows.

### 5. Cache-Then-Refresh Feature Flags and Runtime Configuration

Claude Code's GrowthBook integration reads cached feature values synchronously, then refreshes from the network and notifies subscribers. This lets startup be fast while long-lived systems update when config changes.

Developer Hub has environment-driven and frontend state-driven behavior, but the mission runtime would benefit from a unified runtime configuration layer.

Why it matters:

- Missions can run long enough for policy, feature, or rollout state to change.
- Startup should not block on optional flag services.
- AgentHub needs a clean way to roll out experimental skills, UI panels, approval policies, and verification strictness.

Recommended AgentHub design:

- Add a backend `RuntimeConfigService` with disk or SQLite cache, version, TTL, and refresh subscription.
- Load cached config at process start.
- Refresh asynchronously and emit a trace event when config changes.
- For mission-critical security gates, define whether they are start-of-mission fixed or live-refreshable.
- Expose sanitized config state to frontend only where user-facing.

Priority: medium.

### 6. Token Refresh and MCP Session Expiration Recovery

Claude Code's MCP client detects OAuth 401s, can clear auth caches, wraps fetch for step-up detection, and specifically detects MCP session expiration by HTTP 404 plus JSON-RPC error code `-32001`.

Developer Hub's `useMissionStream.ts` is already resilient for SSE: it waits for Fabric token, reconnects, uses `lastEventId`, falls back to persisted event polling, and recovers terminal state. The remaining gap is a more explicit token provider and remote MCP session-expiration path.

Why it matters:

- Fabric tokens can expire while a mission is still running.
- Remote MCP servers may expire session ids independently of the AgentHub session.
- A single expired remote MCP session should not fail the whole mission if it can reconnect safely.

Recommended AgentHub design:

- Frontend: use a token provider callback rather than storing one token in a ref for the whole stream lifecycle.
- Backend: when an MCP call receives 401, refresh OBO/delegated tokens if possible, mark server `needs_auth` if not, and emit a recoverable event.
- Remote MCP: detect HTTP 404 plus JSON-RPC `-32001`, discard the cached session, reconnect, and retry idempotent/list/read calls once.
- Mission Control: distinguish "waiting for auth", "auth expired", and "MCP session refreshed" from generic tool failure.

Priority: medium, high for remote MCP or very long missions.

### 7. Built-In Plugin Registry with User or Admin Toggle

Claude Code has a built-in plugin registry. Built-in plugins can provide skills, hooks, and MCP servers, and users can enable/disable them through settings. Plugin IDs use a separate `@builtin` source marker.

Developer Hub has a skill/agent catalog but no equivalent toggleable plugin layer.

Why it matters:

- AgentHub will likely accumulate optional capabilities: advanced diagnostics, synthetic data generation, specialized verifiers, Power BI authoring, inventory solution builders, and migration helpers.
- Not every tenant or user should see every experimental capability.
- A plugin layer gives rollout and support a smaller blast radius.

Recommended AgentHub design:

- Introduce `CapabilityPack` or `AgentHubPlugin` metadata for grouped skills, agents, hooks, MCP servers, and UI panels.
- Support built-in packs first; marketplace/user packs can come later.
- Allow admin default state plus user/session override where policy permits.
- Feed enabled packs into the existing capability validator.
- Emit `capability_pack_enabled` and `capability_pack_disabled` events for audit.

Priority: medium.

### 8. Persisted Large Tool Results and Artifact References

Claude Code uses `toolResultStorage.ts` to persist large tool outputs under the session directory. The model receives a bounded preview and a pointer to the full content. This prevents context blow-ups while keeping the full evidence available.

Developer Hub truncates and fences tool output, and it already has artifact and change events. The missing piece is a general persisted-output reference pattern for any large tool result.

Why it matters:

- Fabric tool outputs can be huge: schema lists, report JSON, semantic model metadata, notebook output, validation logs, and inventory scans.
- Truncation alone loses evidence.
- Raw full output in the model context increases cost and prompt-injection risk.

Recommended AgentHub design:

- Persist large raw tool output into the session event store or an artifact store with redaction metadata.
- Return a preview plus an `artifactId` or `toolOutputRef` to the model and UI.
- Add access checks so only authorized users can retrieve full output.
- Record digest, byte count, MIME/type hint, preview, and retention policy.

Priority: medium.

## Low-Priority or Contextual Practices

### 9. Channel-Based Permission Relay

Claude Code can route permission prompts through external channels and race replies against local UI. This is useful for chat-first operation.

Developer Hub is browser-first inside Fabric. The current approval-card path is the right primary UX.

Recommendation: defer unless AgentHub gets Teams/mobile approvals. If adopted, approvals must still flow through the same backend event and audit system.

Priority: low.

### 10. Cleanup Registry

Claude Code has a global cleanup registry with unregister callbacks. Developer Hub already uses FastAPI lifecycle patterns and per-request MCP process cleanup.

Recommendation: only add an explicit cleanup registry if AgentHub introduces long-lived background resources that are not naturally owned by FastAPI lifespan, mission lifetime, or context managers.

Priority: low.

### 11. Background and Foreground Session Switching

Claude Code supports foregrounding/backgrounding local agent tasks because the terminal is a single interactive surface.

Developer Hub missions are naturally backgrounded on the server and viewed through Mission Control. The web equivalent is not Ctrl+B; it is session list, reconnect, persisted events, notifications, and foreground focus.

Recommendation: keep investing in session overview, browser notifications, and reliable resume rather than copying terminal foregrounding literally.

Priority: low.

## Detailed Practice Matrix

| Practice | Claude Code pattern | Developer Hub status | Recommended action | Priority |
| --- | --- | --- | --- | --- |
| Central tool execution context | `ToolUseContext` carries tools, state, permissions, abort, progress, MCP, memory, and UI callbacks. | Partial equivalent through `tool_runtime.py`, orchestrator context, and MCP execution context. | Maintain one tool boundary; consider richer typed context objects around AgentHub tool calls. | Maintain |
| Tool policy registry | Layered allow/deny/ask rules and permission modes. | Strong in `tool_runtime.py`: policy registry, sensitivity, auto-allowed, deny-by-default. | Keep all new tools in registry. | Maintain |
| Identity scrubbing | Permission/tool context separates model input from trusted app state. | Strong: drops caller identity args and pins workspace from verified context. | Maintain and test. | Maintain |
| Untrusted output fencing | Tool/MCP outputs treated as untrusted data. | Strong: explicit fence markers and max output cap. | Maintain; add persisted large-output refs. | Maintain plus medium improvement |
| Circuit breaker | Repeated tool calls and permission loops are controlled. | Strong for identical tool calls; missing denial-loop damping. | Add denial tracking for approvals. | Medium/high |
| MCP server approval | Project servers remain pending until user approval. | Missing for dynamic MCP sources. | Add pending/approved/rejected server gate. | High |
| Diagnostics baseline | Capture diagnostics before edit and show only new diagnostics after. | Missing as a generic Fabric item/definition service. | Add baseline/new/resolved diagnostic events. | High |
| Feature flags | Cache values, refresh async, notify subscribers. | Partial. | Add runtime config cache and refresh hooks. | Medium |
| Scoped memory | User/session/repo memory with loaded index and caps. | Missing beyond session persistence. | Add user/workspace/repo/session memory taxonomy. | Medium |
| Plugin registry | Built-in plugins can be enabled/disabled. | Missing. | Add capability packs feeding existing validation. | Medium |
| Token/session recovery | OAuth 401 handling and MCP session expiration detection. | Frontend SSE recovery is strong; MCP/auth refresh partial. | Add provider refresh and remote MCP reconnect. | Medium |
| Large tool output persistence | Persist full outputs, return preview and pointer. | Partial through events/artifacts; not general for raw tool output. | Add `toolOutputRef` artifact pattern. | Medium |
| Cleanup registry | Global cleanup handlers with unregister. | Partial through FastAPI/context managers. | Defer unless long-lived resources grow. | Low |
| Channel approvals | External-channel permission relay. | Missing. | Defer unless Teams/mobile workflows are in scope. | Low |
| Background/foreground tasks | Terminal task foregrounding. | Web product already has server-side missions. | Prefer session resume and notifications. | Low |

## Recommended Roadmap

### Phase 1: Trust and Diagnostics

Implement the two most important missing controls:

1. MCP server approval states for any non-built-in MCP source.
2. Diagnostic baseline tracking for Fabric definitions and item writes.

This phase improves trust immediately without changing the mission architecture.

Suggested new event types:

```json
{
  "type": "mcp_server_approval_required",
  "serverId": "workspace-powerbi-tools",
  "source": "workspace",
  "toolsPreview": ["read_model", "publish_model"],
  "risk": "Tools can read and modify Fabric items"
}
```

```json
{
  "type": "diagnostic_new_issues",
  "itemId": "...",
  "partPath": "definition/report.json",
  "baselineCount": 2,
  "newIssueCount": 1,
  "issues": [
    { "severity": "error", "code": "SchemaViolation", "message": "..." }
  ]
}
```

### Phase 2: Long-Running Mission Resilience

Add the pieces that matter most when a mission runs long enough to cross token expiry, config changes, and repeated user decisions:

1. Runtime config cache-then-refresh.
2. Fabric token provider refresh in the frontend stream and backend tool calls.
3. MCP session-expiration detection and one safe reconnect/retry.
4. Denial tracking for repeated approval failures.

### Phase 3: Memory and Capability Packs

Once the trust and resilience layers are in place, add productivity features:

1. Scoped memory for user/workspace/repo/session facts.
2. Capability packs with admin/user toggles.
3. General persisted large-output references.

This phase should reuse existing session storage, event ledger, and capability validation instead of creating separate systems.

## Design Cautions

### Do Not Copy Terminal Patterns Literally

Claude Code's foreground/background controls, Ink permission dialogs, and local file diagnostics are terminal-native. Developer Hub should translate the intent into browser-native and Fabric-native primitives:

- Mission Control events instead of terminal rows.
- Approval cards instead of Ink prompts.
- Fabric definition diagnostics instead of only LSP file diagnostics.
- Session resume and notifications instead of Ctrl+B foregrounding.

### Keep Security Decisions Backend-Authoritative

Claude Code can keep more trust state locally because it is a local CLI. Developer Hub is multi-user and hosted. Approval state, MCP server trust, tool policy decisions, memory writes, and diagnostic provenance should be persisted server-side and audited.

### Keep the Event Ledger as the UI Contract

Developer Hub already has the right backbone. New systems should emit structured events and let Mission Control reduce them. Avoid adding separate frontend-only sources of truth for approval, diagnostics, config, or memory.

### Preserve Operator Controls

The current tool runtime has kill switches and deny-by-default behavior. Any plugin, dynamic MCP, or memory-backed behavior should preserve admin controls and safe defaults.

## Bottom Line

Fabric Developer Hub already matches Claude Code on several core safety foundations: tool policy, identity boundaries, event ledgers, capability validation, and output fencing. The next parity step is not another streaming primitive. It is user trust over time.

The highest-value additions are MCP approval gates and diagnostic baselines. After that, add scoped memory, runtime config refresh, auth/session recovery, denial tracking, plugin toggles, and persisted large-output references. Together, these would make Mission Control feel less like a live log viewer and more like a durable, inspectable, recoverable agent operating system for Fabric work.