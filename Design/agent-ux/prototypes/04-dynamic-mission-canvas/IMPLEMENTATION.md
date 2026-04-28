# 04 — Dynamic Mission Canvas · Implementation Notes

These notes translate the prototype into product work. They assume the backend is moving toward a dynamic generalist model that can branch out to catalog agents as needed.

## 1. Compose Page

- Replace the review-plan mental model with a direct run start.
- Button copy: **Start run**.
- Keep mission policy explicit before execution:
  - require approvals
  - allow parallel specialists
  - capture artifacts and evidence
- The backend should create a mission immediately from the prompt and policy, then redirect to the session route.

## 2. Session Page Information Architecture

The session page becomes the only live surface after run start.

Recommended layout:

- Sticky mission header: goal, session id, elapsed time, pause/stop/steer controls.
- Main agent canvas: generalist plus dynamic subagent cards.
- Right rail: mission state, action feed, approvals, selected-agent details, steering directives.
- Completion area: item/action changes, agent returns, caveats, and full logs.

## 3. Required Frontend Data Contracts

The UI needs more than a flat event log. Suggested shapes:

```ts
type MissionAgentRun = {
    id: string;
    taskId: string;
    agentId: string;
    displayName: string;
    role: "generalist" | "specialist";
    status: "preparing" | "spawning" | "running" | "waiting" | "done" | "blocked" | "failed" | "cancelled";
    objective: string;
    startedAt?: string;
    completedAt?: string;
    parentAgentRunId?: string;
    lane?: number;
    logSummary?: string;
};

type MissionEvent = {
    sequence: number;
    timestamp: string;
    type:
        | "generalist_decision"
        | "subagent_spawned"
        | "subagent_log"
        | "subagent_steered"
        | "subagent_cancelled"
        | "artifact_created"
        | "fabric_item_created"
        | "fabric_item_updated"
        | "fabric_item_deleted"
        | "important_action"
        | "approval_required"
        | "approval_resolved"
        | "agent_result"
        | "mission_completed";
    agentRunId?: string;
    taskId?: string;
    title: string;
    detail?: string;
    severity?: "info" | "success" | "warning" | "error";
    targetRef?: {
        kind: "workspace" | "lakehouse" | "warehouse" | "semanticModel" | "report" | "pipeline" | "capacity" | "tenantSetting" | "artifact";
        id?: string;
        name: string;
    };
};

type AgentResult = {
    agentRunId: string;
    status: "success" | "partial" | "blocked" | "failed" | "cancelled";
    summary: string;
    artifacts: Array<{ name: string; kind: string; url?: string }>;
    evidence: Array<{ title: string; detail: string }>;
    errors: Array<{ title: string; detail: string }>;
    caveats: string[];
    followupTasks: Array<{ title: string; objective: string }>;
};
```

## 4. Event Rendering Rules

- `generalist_decision` renders in the generalist card and the action feed.
- `subagent_spawned` creates a card in `spawning` state with a context-pack chip.
- `subagent_log` appends to the matching agent card log. The card should keep recent lines visible without growing the whole canvas indefinitely.
- `artifact_created` and Fabric item mutations render as chips on the responsible agent and in the right-rail feed.
- `subagent_steered` renders as a directive card with the reason, evidence, and message.
- `agent_result` collapses the agent into a result summary while preserving access to logs.
- `approval_required` takes over the right rail but should not hide the canvas.

## 5. Interaction Priorities

- Click an agent card: select it in the right rail and show full logs, context pack, outputs, and result.
- Send steering note: addressed to the generalist by default, not directly to a subagent unless the generalist exposes a branch-level action.
- Pause/stop: mission-level controls stay in the header.
- Abandon subagent: branch-level destructive action, approval or confirmation recommended.

## 6. Acceptance Criteria

- The user can understand which agents exist, which are running, and why each was spawned.
- The generalist's decisions are visible without opening a raw log file.
- Parallel subagents can be seen at the same time with independent logs.
- Created/updated/deleted Fabric items and important actions are visible both in context and in the final summary.
- Steering is represented as a first-class event with evidence and resulting directive.
- Completion includes caveats and partial/blocked branch results, not only green success states.