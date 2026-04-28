# 04 — Dynamic Mission Canvas

Fresh prototype for the post-plan-review AgentHub flow. Page 1 starts the run directly. Pages 2-6 are snapshots of one live mission surface as the generalist discovers work, spawns specialists, monitors them, steers a struggling branch, merges results, requests approval, and completes the mission.

The HTML is also a self-running offline simulation. Open `index.html`, click **Start run**, and the page advances from start to completion using only client-side JavaScript and CSS animations. No backend, websocket, or dev server is required for the flow demo itself.

This is intentionally different from `03-mission-control`: the user no longer approves a fixed upfront team. The user approves the run policy on the compose page, then watches a live generalist-controlled canvas evolve.

Every live run screen keeps the original task context visible at the top: the entered prompt, workspace, referenced Fabric items, file attachments, and run configuration are collapsed by default and can be expanded in place. The former large fake page title is replaced with a compact high-level step overview so the user can see what is currently executing without pushing the canvas out of view.

## Flow

| # | State | What changed |
|---|---|---|
| 1 | Start run | The primary CTA is **Start run**, not **Plan this**. The toggles set mission policy: approvals, parallel specialists, artifact capture. |
| 2 | Generalist boot | The generalist appears first, streams a larger log, creates context packs, and starts the first specialist. |
| 3 | Parallel wave | Three specialists run concurrently under the generalist. Each card shows live logs plus produced items, artifacts, action candidates, and risk chips. |
| 4 | Steering | The generalist inspects repeated subagent logs, uses new context from another branch, and steers the branch instead of blindly waiting. |
| 5 | Merge and approval | Subagents return structured summaries. The generalist turns them into an approval-gated action group. |
| 6 | Complete | The final surface emphasizes created/updated/deleted items, important Fabric actions, agent returns, caveats, and full run logs. |

## Design Position

- **The generalist is the anchor.** It is always visible as the running controller, not just a label above a graph.
- **Logs are first-class.** Every agent card carries a local log pane, and the generalist gets extra vertical space because its decisions explain the run.
- **Outputs sit with the agent that caused them.** Created artifacts, updated Fabric items, resource locks, approvals, and important actions are shown as chips directly under the relevant agent.
- **The right rail is a change ledger.** It intentionally avoids generic activity feed noise and focuses on created, updated, deleted, and important non-item actions such as settings or capacity changes.
- **Parallelism is visible.** Specialist cards appear as simultaneous branches rather than a predeclared topology.
- **Task context stays available.** The collapsed prompt recap mirrors the compose context and expands to show the full prompt, workspace, items, attachments, branch target, and run configuration.
- **The header is an execution summary.** Each run state shows compact high-level steps currently underway rather than a large static title.
- **Logs can grow when needed.** Use the bottom **Large logs** toggle to expand all agent logs, or double-click an individual log to open that agent's larger log focus.
- **Steering is a product moment.** The UI shows the exact evidence that caused the generalist to intervene and the directive it sends.
- **Completion is an audit surface.** The end state is organized around what changed and which agent returned which evidence.

## Files

| File | Purpose |
|---|---|
| `index.html` | Six-screen static clickthrough plus offline start-to-end run demo. |
| `styles.css` | Canvas layout, agent cards, log panes, status chips, approval and completion states. |
| `nav.js` | Review navigation plus the offline auto-run simulation controller. |
| `IMPLEMENTATION.md` | Product implementation guidance for mapping the design into the React workload. |

## Review Notes

The bottom numbered navigation, header screen counter, flow player, and black prototype banner are review chrome only. The product version should be one auto-updating session page after the run starts.