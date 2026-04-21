# 03 — Mission Control (orchestration-graph-first)

Static-but-clickable HTML prototype for the Developer Hub multi-agent workflow.
Built against the shared Developer Hub design system (`../../../_shared/styles.css`
+ sidebar, Tailwind CDN config, Material Symbols, Inter). Fits into the same
look as `session_live_execution.html`, `new_session_step1_compose.html`, and
`agents_and_skills.html`.

> **This is a UX sketch, not a product spec.** It's meant to help us align on
> *how a multi-agent run should feel* — the shape of the information, the
> timing, the trust signals. It is deliberately stripped of real backend
> concerns. Any number, agent name, or timestamp you see is placeholder copy.

## Prototype chrome vs. product chrome

A few things exist only so reviewers can click through the deck — they are
**not** part of the final product:

- **Bottom page-number bar (`1 / 6`, prev / next arrows, numbered crumbs).**
  Purely a review-mode helper. In the real product, users don't step through
  fixed screens — they just watch one live surface update as their run
  progresses. Mentally: imagine screens 3 → 4 → 5 → 6 as *the same page*
  reacting to events over time.
- **"Prototype" pill in the top breadcrumb**, the counter in the header
  (`1 / 6` badge), and the black "Prototype · static content" footer banner.
  Also review-only.
- **The 6 screens themselves.** The product has two real destinations:
  *compose* (screen 1) and *live run* (screens 2 → 6 collapsed into one
  auto-evolving view). Everything in between is just "the same page at
  different moments of the same run."

Everything else — the Developer Hub header, the expanded sidebar, the cards,
the pills, the agent chips, the graph, the log, the approval panel — is
intended to become real.

## End-to-end flow (what actually happens)

The prototype is organized around a single mental model: **a user describes
an outcome; Developer Hub proposes a team of agents; the user approves; the
team works; the user stays in the loop at the few moments that matter.**

1. **User writes a prompt (screen 1).** Plain language — e.g. *"Automate
   weekly ingestion of regional sales data and certify the Gold dataset."*
   They may attach Fabric items (lakehouses, warehouses, notebooks) and a
   reference file, and toggle a couple of run-wide settings (*require
   approvals*, *allow branching*). Nothing runs yet.
2. **System proposes a team (screen 2).** The prompt is parsed into a
   proposed architecture — *"1 Orchestrator supervising 3 specialists"* —
   rendered as a read-only orchestration graph. The user sees *who* will
   work on *what*, with skills, roles, and the planned handoffs. They can
   **Regenerate**, **Edit agents**, or **Compare architectures** before
   committing. Crucially, no tools have been invoked yet. The task prompt
   is preserved in a "recap" strip so context never gets lost.
3. **User approves & the run starts (screen 3).** The same graph stays
   on-screen, but nodes light up as agents come online. A live log streams
   per-agent reasoning, tool calls, and artifacts on the right. The
   currently active agent is surfaced in three places at once (graph ring,
   team strip, log header pill) so the user never has to hunt for "who is
   doing what."
4. **Approvals happen inline (screen 4).** Certain steps — anything
   destructive, anything governance-relevant — pause the run and surface
   an **approval card** with plain-language summary, reversibility note,
   blast radius, and a preview of the tool call. The user approves,
   declines, or requests an alternative. The team waits; no work is lost.
5. **Later phases keep the same surface (screen 5).** As work handoffs
   travel around the graph, the same page updates — no new screen, no
   navigation. Completed agents go green; the new active agent gets the
   blue ring. The log keeps scrolling in the same pane.
6. **Completion (screen 6).** All nodes green, produced artifacts listed,
   a "who did what" contribution summary, and three paths forward:
   **Export**, **Save as template**, **Start another**.

The key idea: the orchestration graph is the *anchor*. It tells the user
what the team looks like before the run, what's happening during the run,
and what was done after the run — without ever switching surfaces.

## Architectures page (`architectures.html`)

The sibling file `architectures.html` (linked from the "Compare architectures"
button on screen 2) is **illustrative, not a page we plan to ship**. It
exists to answer the question *"what architectures can Developer Hub
propose, and when would each one show up?"* in one scroll. Think of it as
design documentation for reviewers, not a UI the user navigates to.

It shows five archetypes (Supervisor, Network, Hierarchical, Sequential,
and a Mixed / composite combination), each rendered as *"what a real run
would look like"* — a user prompt, an orchestration graph, and an execution
log — so we can argue about whether the graph + log visuals read clearly
for each pattern.

| Pattern | Shape | When Developer Hub would propose it |
|---|---|---|
| **Supervisor** | 1 lead → N workers | Clear decomposition, specialists don't overlap. *(The canonical flow in screens 2–6.)* |
| **Network** | All-to-all peers | Domains overlap; peer feedback improves quality (code review, red-team). |
| **Hierarchical** | Lead → sub-leads → workers | Large, domain-sliced task (migrations, platform rollouts). |
| **Sequential** | A → B → C → D | Deterministic pipeline where each stage transforms the previous output (summarize → write → edit). |
| **Mixed / composite** | 1 commander → N sub-teams, each with its own internal pattern | One shape isn't enough. A commander delegates to sub-teams that each run the pattern that fits their sub-task (e.g. incident response: sequential hotfix + network investigation + solo comms, all in parallel). Sub-teams are visually framed with dashed labelled borders. |

In production, the user won't browse this page. They'll just submit a
prompt, and the system will *pick* one of these shapes (and let them
regenerate to a different shape if they disagree). The page is here so the
design team can see all four shapes side-by-side and stress-test the
visual language.

## What it shows

| # | Screen | What's on-screen |
|---|---|---|
| 1 | **Submit task** | Mirrors `new_session_step1_compose.html`: centered hero ("Orchestrate your vision."), rich prompt composer card with decorative glow, three-group context pill row (Fabric items · workspace · file), settings toggles (Require approvals ON, Branch out OFF), action footer with **"Propose team" → `auto_awesome`** primary CTA (not "Generate plan" — the next screen is a team proposal, not an executable plan), plus a 3-card "Start from a playbook" strip. Nothing runs here. |
| 2 | **Propose team (orchestration graph)** | The suggested architecture (supervisor pattern) with four nodes (Orchestrator, FabricDataEngineer, FabricAdmin, SalesReporter), their roles, skills, and interaction edges (delegate / peer handoff). Right rail lists the role each agent plays in *this* task. Enters with a `slide-up` animation. Actions: **Regenerate**, **Edit agents**, **Compare architectures** (opens `architectures.html`), **Run**. |
| 3 | **Run begins** | Persistent **task-prompt recap** strip at the top (collapsed; click to expand). Below it, a **collapsible team panel** showing either the compact team strip (default) or the full orchestration graph (when expanded), followed by the two-pane live-log + inspector area. Orchestrator green, FabricDataEngineer pulsing active, others planned. Active edges animate. The expanded panel is height-capped (`min(520px, 42vh)`) with internal scroll so the log area stays visible. |
| 4 | **Handoff + approval** | FDE done (green), FabricAdmin in "waiting on you" state, peer-edge FDE→Admin animated. Right pane is a full approval panel with plain-language summary, reversibility, blast radius, tool-call preview, and four recovery actions. Primary CTA "Approve & certify" in the live `rounded-xl shadow-lg shadow-primary/15` style. |
| 5 | **Later stage** | FDE + Admin green, SalesReporter now active. Same surface, same team panel, same log pane — just with newer state. |
| 6 | **Complete** | All four nodes green, artifacts produced list, "who did what" contribution summary, **filterable full run log** (per-agent tabs), Export / Save template / Start another. |

## How this differs from the live Fabric workload (`session_live_execution.html`)

The prototype inherits the Developer Hub design system wholesale — same
header, same sidebar, same pills, same streaming-log shape — but the
run-time *information architecture* is intentionally different. These are
the deliberate deltas; each one is something we'd like to change in the
real product.

| Area | Live workload today | Mission Control prototype | Why we want the change |
|---|---|---|---|
| **CTA on compose** | "Generate plan" (implies the next screen will execute). | **"Propose team"** (screen 2 is a team/plan *proposal* you review before anything runs). | Removes the rug-pull between "I hit a button" and "tools are running." Nothing runs until the user explicitly approves the proposed team. |
| **Between compose and run** | Plan is textual / listy; the run screen is where agents "appear." | A dedicated **orchestration-graph screen** showing the proposed team (nodes + edges + roles) *before* the run starts. User can **Regenerate**, **Edit agents**, or **Compare architectures**. | Makes the team visible and editable *before* spending tokens/tool calls. The graph is the contract between user and system. |
| **Run surface** | Multiple pages (plan → live → result). | **One surface** that evolves through phases (run → approval → later stage → complete). Screens 3–6 in the prototype are snapshots of the same page at different moments. | Users shouldn't have to re-orient every time the phase changes. The graph is the anchor; everything else fills in around it. |
| **"Who is working right now?"** | Implicit — you have to read the log. | **Triple-surfaced**: graph node ring + team-strip chip + log header pill, all in lockstep. | "Which agent?" should never require a scan. |
| **Task context during a run** | Scrolls away. | **Persistent task-prompt recap** (collapsed bar at top of screens 3–6, click to expand full prompt + attachments). | The original ask shouldn't disappear the moment work starts — reviewers and operators need it at hand. |
| **Team visibility during a run** | Agents surface only through log lines. | **Collapsible team panel**: a compact one-line strip by default (Orchestrator → workers via a light chevron connector), expandable into the full orchestration graph with a height cap + internal scroll so it never pushes the log off-screen. Topology variants (`supervisor` / `sequential` / `network` / `solo`) reshape the strip to match the actual pattern being run. | Keeps the who/what/pattern visible at all times without monopolising the viewport. |
| **Approvals** | Generic "approve this step" pause. | **Inline approval card** scoped to *one* tool call: plain-language summary, reversibility note, blast-radius chip, exact tool-call preview, and four recovery actions (Approve / Decline / Request alternative / Edit input). | Approvals are trust surfaces. Users should see the blast radius and the exact call before they say yes. |
| **Log** | Single flat stream. | Same stream shape during a run, plus a **full-run log with per-agent filter tabs** on screen 6 so reviewers can scope post-mortem reading to one agent at a time. | Post-run review is a different job than in-flight monitoring; it deserves its own affordances. |
| **Completion** | "Done" state. | **Artifacts list + contribution summary + three forward paths** (Export / Save template / Start another) so the work has somewhere to go. | A completed run is a template-in-waiting; we want that to be one click, not a separate workflow. |
| **Pattern selection** | Not a user-facing concept. | Explicitly named and browsable via `architectures.html` (Supervisor / Network / Hierarchical / Sequential / Mixed). The system picks one from the prompt; user can regenerate to a different shape. | Makes the *shape* of the team a first-class concept the user can reason about. |

Everything **outside** this table — header chrome, sidebar, tokens,
typography, icon set, approval-button style, pill vocabulary — is
deliberately identical to the live workload. Only the information
architecture is reimagined.

## Design fidelity to the live workload

Screen 1 is a direct structural clone of `Design/new_session_step1_compose.html`:
same hero pattern (10×10 tinted icon + `dh-h1` + centered `dh-subtitle`),
same `max-w-3xl` composer card with `shadow-[0_2px_12px_rgba(0,0,0,0.04)]`
and decorative `bg-primary/5 blur-3xl` glow, same `fabric-badge-*` pill
colors, same three-group separator dividers, same toggle-track visuals, same
"Propose team" primary button with `auto_awesome` icon. The top header
carries the centered search bar and the sidebar renders expanded via
`renderAgentHubSidebar('new-session')` — exactly like the live page.

Screens 2–6 carry the same visual grammar forward: `dh-section-label`
eyebrows (tinted icon + `uppercase tracking-[0.15em]`), softer card shadow,
and the `rounded-xl shadow-lg shadow-primary/15` style on every primary
CTA. The orchestration graph itself is bespoke to this prototype but lives
inside a card that reads as "just another Developer Hub surface."

## The orchestration graph

- Read-only SVG visualization — **not** a node editor. The system proposes an
  architecture; the user can Regenerate, Edit agents, or Run it.
- Nodes map 1:1 to agents from the `agents_and_skills.html` roster, plus an
  Orchestrator supervisor.
- Edge kinds: dashed blue = *delegate & report*, solid green = *report back*,
  dashed orange = *peer handoff*. Active edges animate with a dash-shift.
- Node states: `planned` (dashed border), `active` (blue ring + pulse dot),
  `done` (green fill), `waiting` (amber fill).
- On live screens (3–5), the graph is wrapped in a **team panel** that
  toggles between a one-line compact team strip (default) and the full
  graph (expanded). The expanded view is capped at `min(520px, 42vh)` with
  internal scroll so the live log underneath is never pushed off-screen.
- The compact strip uses a single light-blue chevron (`›`) as the lead
  → workers connector (not a dashed line with an endpoint dot, which read
  as "unfinished").

## Which agent is at work

Every live screen surfaces the currently active agent in three places
simultaneously:

1. In the **graph** — the node has a blue ring + pulse dot, and the incoming
   edge is animated dashed blue.
2. In the **identity strip** (top of the run panel) — e.g. "active:
   FabricDataEngineer".
3. In the **log header pill** — an `mc-pill--running` chip with the agent
   name next to the "Live log" title.

## Files

| File | Purpose |
|---|---|
| `index.html` | The 6-screen mainline clickthrough (compose → propose team → run → approval → later → complete). |
| `architectures.html` | Illustrative side-by-side of the five multi-agent patterns Developer Hub can propose (Supervisor, Network, Hierarchical, Sequential, Mixed/composite). Linked from the "Compare architectures" button on screen 2. Not a shipping page. |
| `styles.css` | Prototype-only additions on top of `_shared/styles.css` — orchestration-graph nodes/edges/legend, team-panel chrome (incl. height-capped expanded view and chevron connector), topology variants for the team strip, run log filters, approval card, node-state colors. |
| `nav.js` | Prev/Next, numbered crumbs, arrow-key navigation, `data-goto="N"` jumps. Review-mode only — *not* part of the product. |
| `team-panel.js` | Drives the collapsible team panel (expand/collapse, overflow chip). |
| `strings.js` | Minimal visible-strings bag. |

## Design principles this prototype is trying to demonstrate

These are the ideas the visuals are meant to make legible. When we argue
about detail choices, these are the anchors:

- **The user is always in the loop, but never in the way.** The run
  auto-advances unless a step needs judgement. Approvals are inline and
  scoped to *one* tool call at a time.
- **"Who is doing what" is never a riddle.** The active agent is visible in
  three places simultaneously (graph ring, team strip, log header pill).
- **The graph is read-only.** Users pick *intent*, not wiring. The system
  chooses the architecture and the user can Regenerate or Edit agents — but
  we never ship a node editor.
- **Same surface, different moments.** Live run → approval → later phases
  → completion is one page evolving, not a navigation flow.
- **Trust signals are first-class.** Every approval shows reversibility,
  blast radius, and a preview of the exact tool call. Nothing is hidden
  behind "Run".
- **Every state has a text label.** Color is reinforcement, not the
  carrier. "Running", "Awaiting you", "Done" are in the markup.

## How to view

Open `index.html` directly in any modern browser, or navigate from
`../index.html` (the prototype landing).

```
./index.html             ← open this (the 6-screen deck)
./architectures.html     ← illustrative architecture comparison
../index.html            ← landing page that links here
```

No build step, no local server, no network calls.

## Accessibility

- All navigation controls are real `<button>` elements.
- Graph nodes are focusable (`tabindex="0"`) and the SVG edge layer carries
  `aria-hidden="true"` so screen readers don't read decorative geometry.
- `prefers-reduced-motion: reduce` disables the pulse, dash-shift, and
  typing-cursor animations.
- Color is never the only state carrier — every state has a text label
  ("Running", "Done", "Awaiting you") in the node body and an icon in the
  log markers.

## Hard rules honored

- No fake streaming, no simulated runtime — every animation is CSS only and
  nothing fetches anything.
- No auth, no backend.
- The orchestration graph is **read-only** — a visualization, not a node
  editor. The user cannot wire agents by hand.
- Fits the shared Developer Hub design system: same header, same sidebar,
  same tokens, same Material Symbols, same streaming-log shape as
  `session_live_execution.html`.
