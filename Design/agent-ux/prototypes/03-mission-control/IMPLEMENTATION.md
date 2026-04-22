# 03 — Mission Control · IMPLEMENTATION

Agent-actionable checklist for rolling the prototype's information-architecture
changes into the live Developer Hub workload. **Read `README.md` first** for
intent and rationale — this file is the *what/where/in what order*, not the
*why*.

> **Audience:** an engineer or coding agent implementing against the React
> workload. **Scope:** information architecture and copy only. The visual
> token system (header, sidebar, pills, buttons, typography) is already
> shared and is explicitly **not** being changed here.

---

## 0 · Codebase orientation

- **Framework:** React + `@fluentui/react-components` + `react-router-dom`
  v5. TypeScript throughout. CSS in `styles.scss`.
- **i18n:** `react-i18next`. Strings live under
  `Frontend/src/internalAssets/locales/{en-US,es,he}/translation.json`.
  New user-visible strings MUST be added to all three locales (English copy
  for `en-US`; leave `es` and `he` with the English string + a
  `TODO(i18n)` comment so the localization team picks them up).
- **Routes** (`Frontend/src/components/AgentHub/AgentHubLayout.tsx`):
  - `/agent-hub/home` → `DashboardPage` (list of sessions)
  - `/agent-hub/orchestrator` → `OrchestratorPage` (**compose + plan review**, prototype screens 1–2)
  - `/agent-hub/session/:sessionId` → `SessionDetailPage` (**live run / history**, prototype screens 3–6)
  - `/agent-hub/agents` → `AgentsPage` (agent roster)
  - `/agent-hub/settings` → `SettingsPage`
- **Backend API** the UI calls: `Frontend/src/controller/AgentHubApi.ts`.
  Backend source in `Backend/src/`.
- **Plan wire format:** camelCase, mirrored in
  `Frontend/src/components/AgentHub/plan/types.ts` ↔
  `Backend/src/domain/models/plan.py`. **Any new field added on one side
  MUST be added to the other in the same PR.**

### Do-not-touch list

The following are out of scope for this workstream. If you think you need
to change one of them, stop and ask:

- `AgentHubLayout.tsx` header / sidebar / routing table.
- Auth flow (`useGitHubAuth.ts`, `callAuthAcquireAccessToken`, Fabric OBO).
- `WorkspacePreviewModal.tsx`.
- `PbiFixer/*` (separate feature).
- `DashboardPage.tsx` (session list) — unchanged.
- `AgentsPage.tsx` / `SettingsPage.tsx`.
- The shared design tokens in `styles.scss` above line ~5000.

---

## 1 · Ship order (phases)

Phases are ordered **cheapest + safest first**. Each phase is independently
shippable behind its own feature flag. **Do not skip ahead** — each phase
assumes the previous one landed.

| Phase | Scope | Rough size | Backend work? |
|---|---|---|---|
| **P1** | Copy-only: rename "Generate Plan" → "Plan this". | Single-line | No |
| **P2** | Persistent task-prompt recap strip on `SessionDetailPage`. | Component + CSS | No |
| **P3** | Collapsible team-panel chrome (compact strip + expandable graph, chevron connector). Topology variants follow `team.pattern`. | Component + CSS + new field | **Yes**, small |
| **P4** | Inline approval card (plain-language summary, reversibility, blast radius, tool-call preview). | Component + CSS + new fields | **Yes**, medium |
| **P5** | Single-surface run evolution — fold plan review and live run into one page state machine. | Layout refactor | No |
| **P6** | Filterable full-run log on completed sessions (per-agent tabs). | Component + filter logic | No |
| **P7** | Pattern selection UX — expose the chosen architecture and allow regenerate-as-different-pattern. | Component + new field + backend | **Yes**, large |

Feature flags should live next to the existing settings/flags pattern
(grep `require_approvals` / `branch_out` for the existing plumbing in
`OrchestratorPage.tsx`) and default **off** in production until QA signs off.

---

## 2 · Per-phase checklist

Each entry lists: **target file(s)** · **change** · **acceptance criteria**
· **out-of-scope**.

### P1 · Copy rename: "Generate Plan" → "Plan this"

The next screen is a *proposal*, not an executable plan. This is the
smallest change with the clearest payoff.

- **File:** `Frontend/src/components/AgentHub/OrchestratorPage.tsx`
  - **Line ~2581:** change button label
    ```diff
    - {planning ? <Spinner size="tiny" /> : "Generate Plan"}
    + {planning ? <Spinner size="tiny" /> : t("Compose_Submit")}
    ```
  - **Line ~1628:** update the code comment
    ```diff
    - // effect on the next "Generate Plan" click.
    + // effect on the next "Plan this" click.
    ```
- **File:** `Frontend/src/internalAssets/locales/en-US/translation.json`
  - Add: `"Compose_Submit": "Plan this"`
- **Files:** `es/translation.json`, `he/translation.json`
  - Add the same key with the English string + `TODO(i18n)` — locale team
    will translate.
- **Acceptance:**
  - Compose page button reads **Plan this**.
  - Button still disables on the same conditions (`planning`, empty
    `taskText`, no workspace, invalid git config).
  - Click still calls `handleGeneratePlan`.
  - All three locales resolve the key.
- **Out of scope:**
  - Do not rename `handleGeneratePlan`, `planning` state, `planState`, or
    any backend endpoint. Internal names stay as-is.
  - Do not change the `Sparkle24Regular` icon.

### P2 · Persistent task-prompt recap on the live session page

Today `SessionDetailPage` shows the task description once in the header
(`job-goal-text`) and it scrolls away as the log grows. Turn that into a
sticky, collapsible recap that mirrors the prototype's
`.mc-prompt-recap`.

- **Target file:** `Frontend/src/components/AgentHub/SessionDetailPage.tsx`
- **Current markup (around line 373):** the `job-detail-header` block
  that renders `<Body1 className="job-goal-text">{job.task_description}</Body1>`.
- **Change:** introduce a new component `TaskPromptRecap` that renders
  between the header and the `job-split-pane`:
  - Collapsed by default — one line: icon + truncated task text + attachment badges.
  - Click to expand — full task text + workspace pill + attached Fabric
    items + attached files.
  - Sticky inside the scroll container (same position as prototype).
  - Use `<details>`/`<summary>` or a controlled React component; either
    is fine as long as keyboard + `Enter`/`Space` both toggle.
- **Data sources (already on `job`):**
  - `job.task_description` — prompt text.
  - `job.context.workspace_snapshot` — attached Fabric items.
  - `job.context.attachments` — attached files (if populated).
  - `job.workspace_id` / resolved name.
- **Acceptance:**
  - On every live screen (running / waiting / completed / cancelled) the
    recap strip is visible above the split pane.
  - Collapsed height ≤ 44 px. Expanded height grows with content.
  - Screen-reader: the summary row is a button; `aria-expanded` reflects
    state; content region is labelled.
  - `prefers-reduced-motion: reduce` disables any height transition.
- **Out of scope:**
  - Do not move the existing header (`job-detail-header`) — the recap sits
    *below* it.
  - Do not add editing affordances. The recap is read-only.
  - Do not alter `Caption1` copy on the original header row.

### P3 · Collapsible team panel + chevron connector + topology variants

The live page has a thin `collaborators-bar` with Fluent badges. Replace
it with the prototype's team panel: a compact one-line strip by default,
expandable to the full orchestration graph, height-capped so the log area
below never gets pushed off-screen.

- **Target files:**
  - `Frontend/src/components/AgentHub/SessionDetailPage.tsx` — replace
    the `collaborators-bar` block (~line 411) with a `<TeamPanel>` import.
  - **New:** `Frontend/src/components/AgentHub/team/TeamPanel.tsx` —
    panel chrome (bar + expand toggle + compact strip + expanded canvas).
  - **New:** `Frontend/src/components/AgentHub/team/TeamStrip.tsx` —
    compact horizontal chip row with chevron connector.
  - **New:** `Frontend/src/components/AgentHub/team/OrchCanvas.tsx` —
    the SVG graph (nodes + edges). Start with the prototype's
    `.orch-canvas` markup transliterated into React; node positions can
    be computed from `team.nodes` rather than inlined.
  - `Frontend/src/styles.scss` — copy the `.mc-team-panel*`,
    `.mc-team-strip*`, `.mc-agent-chip*`, `.orch-canvas`, `.orch-node*`,
    `.orch-edge*` blocks from
    `Design/agent-ux/prototypes/03-mission-control/styles.css`.
    **Prefix everything with `.dh-` or nest under a `.team-panel-root`
    to avoid collisions.**
- **New backend field required:** on the session / plan response, add
  - ```ts
    team: {
        pattern: "supervisor" | "sequential" | "network" | "solo" | "mixed";
        nodes: Array<{
            id: string;
            agent: string;          // agent key from agents_and_skills
            role: string;           // e.g. "Orchestrator", "Stage 1 · root-cause"
            status: "planned" | "active" | "done" | "waiting";
        }>;
        edges: Array<{
            from: string;
            to: string;
            kind: "delegate" | "peer" | "report";
        }>;
    }
    ```
  - **Backend:** add `team: Team` to `Plan` (and the session's runtime
    snapshot) in `Backend/src/domain/models/plan.py`. Populate from the
    orchestrator's planning output. This is a *new* concept; the
    backend has no pattern/topology field today.
  - **Frontend mirror:** add matching interface to
    `Frontend/src/components/AgentHub/plan/types.ts`.
- **CSS fixes already applied in the prototype (copy verbatim):**
  - Connector: single chevron (`›`), not a dashed line with endpoint dot
    (which read as "unfinished").
  - Expanded view: `max-height: min(520px, 42vh)` with `overflow-y: auto`
    and the embedded `.orch-canvas` forced to `height: 360px` so the log
    area below stays visible.
- **Acceptance:**
  - Collapsed strip fits on one line on ≥ 1024 px viewports; wraps on
    narrower. Keyboard focus order: bar → expand button → chips.
  - Clicking the expand toggle swaps strip ↔ graph without page jump.
  - Graph renders for each of the five patterns
    (supervisor/sequential/network/solo/mixed) from real `team.nodes` +
    `team.edges`. Mixed uses dashed purple sub-team frames matching the
    prototype.
  - When expanded, the live log below is still visible and scrollable.
  - Active node has blue ring + pulse dot AND the matching log header
    pill (from P2/existing logic) names the same agent. "Who is active"
    must match across all three surfaces (graph ring, strip chip, log
    pill).
- **Out of scope:**
  - No node editor. Graph is read-only.
  - No drag-to-reposition, no zoom-pan.
  - Do not introduce a graph library (d3, cytoscape, react-flow).
    Prototype SVG + CSS is sufficient.

### P4 · Inline approval card

Today the closest thing is the plan-review "Approve / Reject" on
`OrchestratorPage`, but mid-run approvals are not surfaced as rich cards.
Build the prototype's approval card for mid-run, per-tool-call approvals.

- **Target files:**
  - **New:** `Frontend/src/components/AgentHub/approvals/ApprovalCard.tsx`
  - `Frontend/src/components/AgentHub/SessionDetailPage.tsx` — when a
    phase surfaces an `awaiting_approval` sub-state, render
    `<ApprovalCard>` in place of the `changes-pane` right column.
  - `Backend/src/domain/models/plan.py` — extend `PlanStep` (and/or a
    new `ApprovalRequest` model emitted at runtime) with:
    - ```py
      blast_radius: Literal["workspace", "item", "row-level", "metadata-only"]
      tool_call_preview: dict  # name + args, redacted
      recovery_actions: list[Literal["approve", "decline", "request_alternative", "edit_input"]]
      ```
    - `reversible` already exists on `PlanStep` — reuse it.
  - Frontend mirror in `plan/types.ts`.
- **Acceptance:**
  - Card shows: plain-language summary (one sentence), reversibility
    badge, blast-radius chip, collapsible tool-call JSON preview,
    four action buttons in a consistent order.
  - Primary CTA uses the existing Fluent `appearance="primary"` style —
    do **not** reintroduce the prototype's Tailwind classes. Translate
    the prototype's visual language into Fluent tokens.
  - Keyboard: Tab order matches reading order; `Esc` does nothing
    (approval is not dismissible — the user must pick an action).
  - Decline/alternative/edit emit the corresponding backend call
    (new endpoints required — coordinate with backend team).
- **Out of scope:**
  - Don't replace the compose-time plan approval in
    `OrchestratorPage → PlanView`. That's a separate, batch-level
    approval and stays as-is.
  - No "approve all future" mega-action. Each approval is scoped to one
    step.

### P5 · Single-surface run evolution

Today the user sees compose → plan review → session list → session
detail. The prototype collapses plan review and live run into one
auto-evolving surface. This is a layout refactor, not new features.

- **Target files:**
  - `Frontend/src/components/AgentHub/OrchestratorPage.tsx` — after
    `handleApprove`, instead of `history.push` to the session detail
    route, **in-place transition** the same page into the run view.
  - `Frontend/src/components/AgentHub/SessionDetailPage.tsx` — keep as
    the permalink / deep-link entry point (so refresh + bookmarking
    still work). The two routes render the **same root component tree**
    with a `mode` prop (`"plan"` | `"run"` | `"complete"`).
- **Acceptance:**
  - After "Approve & run", the user stays on `/agent-hub/orchestrator`
    and the page transitions graph-only → graph + log. No navigation.
  - Deep-linking to `/agent-hub/session/:id` still works for historical
    or resumed sessions.
  - Browser back/forward still works.
  - The persistent task recap (P2) and team panel (P3) carry across
    without remount.
- **Out of scope:**
  - Don't change the route shape. `/session/:id` keeps its URL.
  - Don't remove the session list — it remains the entry point for
    historical sessions.

### P6 · Filterable full-run log on completed sessions

On completion, `phases` can be 50+ entries; filtering by agent is
valuable for post-mortems.

- **Target file:** `Frontend/src/components/AgentHub/SessionDetailPage.tsx`
- **Change:** when `job.status === "completed"`, swap the log's
  existing `TabList` (All/Files/Metadata — which is for the *actions*
  pane) with a new `TabList` scoped to agents. Agent list derived from
  `Object.values(agentStatuses)`.
  - Add an "All agents" tab (default).
  - Clicking a tab filters `phases` to entries whose `owner_agent`
    matches. **New wire field required:** add
    `owner_agent: str | None` to each phase on the backend.
- **Acceptance:**
  - Tabs render only when there are ≥ 2 agents in the run.
  - Filter is purely client-side; no refetch.
  - "All agents" is the default on every visit (don't persist the filter).
  - `aria-selected` is set correctly for a11y.
- **Out of scope:**
  - Don't touch the `changes-pane` tabs. Those are a different concept.
  - Don't add full-text search — that's a bigger feature.

### P7 · Pattern selection & regeneration

This is the largest, most-speculative phase. Only start after P1–P4
ship. It requires the orchestrator to *choose* a pattern from the
prompt and surface that choice.

- **Backend work:**
  - Orchestrator returns `team.pattern` on the plan response (see P3).
  - New endpoint: `POST /sessions/{id}/regenerate-plan` with body
    `{ preferredPattern?: TeamPattern }`. Unblocked by P3's `team` field.
- **Frontend work:**
  - On the plan-review screen (P5 mode `"plan"`), add a "Compare
    architectures" link that opens a drawer (not a new route) with the
    same illustrative content as `architectures.html`.
  - Add a "Regenerate as…" menu on the team-panel bar with the five
    pattern options. Selecting one calls the regenerate endpoint and
    swaps `team.nodes` / `team.edges` in place.
- **Acceptance:**
  - Current pattern name is visible in the team panel's bar meta
    (prototype: `· 4 agents · Supervisor pattern`).
  - Regenerate round-trips in < 3 s on the happy path; while pending,
    show a Spinner and keep the old graph faded at 50 % opacity.
  - If the orchestrator refuses the requested pattern (e.g. prompt is
    fundamentally sequential), surface the refusal reason inline
    ("This task is sequential — peer-network pattern wouldn't apply.")
    and keep the old graph.
- **Out of scope:**
  - Auto-detecting a better pattern mid-run. Pattern is decided at
    plan time and does not change after the run starts.

---

## 3 · Copy deck

All user-visible strings that change. Add these keys to all three locale
files (`en-US`, `es`, `he`). Use `en-US` values below; `es` and `he` get
the English text plus `TODO(i18n)` until the localization team translates.

| Key | Value (en-US) | Used in |
|---|---|---|
| `Compose_Submit` | `Plan this` | `OrchestratorPage.tsx` submit button |
| `Recap_Task_Label` | `TASK` | `TaskPromptRecap` eyebrow |
| `Recap_Expand` | `Show full prompt` | `TaskPromptRecap` expand button title |
| `Recap_Collapse` | `Collapse prompt` | `TaskPromptRecap` collapse button title |
| `TeamPanel_Title` | `Team` | `TeamPanel` bar title |
| `TeamPanel_Meta` | `{{agentCount}} agents · {{patternLabel}} pattern` | `TeamPanel` bar subtitle |
| `TeamPanel_Expand` | `Expand` | Expand toggle |
| `TeamPanel_Collapse` | `Collapse` | Collapse toggle |
| `Pattern_Supervisor` | `Supervisor` | Pattern label |
| `Pattern_Sequential` | `Sequential` | Pattern label |
| `Pattern_Network` | `Network` | Pattern label |
| `Pattern_Solo` | `Solo` | Pattern label |
| `Pattern_Mixed` | `Mixed` | Pattern label |
| `Approval_Title` | `This step needs your approval` | `ApprovalCard` title |
| `Approval_BlastRadius_Workspace` | `Workspace-wide` | Blast radius chip |
| `Approval_BlastRadius_Item` | `Single item` | Blast radius chip |
| `Approval_BlastRadius_RowLevel` | `Row-level` | Blast radius chip |
| `Approval_BlastRadius_MetadataOnly` | `Metadata only` | Blast radius chip |
| `Approval_Reversible_Yes` | `Reversible` | Reversibility chip |
| `Approval_Reversible_No` | `Irreversible` | Reversibility chip (reuses existing `fabric-badge--irreversible` style) |
| `Approval_Action_Approve` | `Approve & continue` | Primary CTA |
| `Approval_Action_Decline` | `Decline` | Secondary |
| `Approval_Action_Alternative` | `Request alternative` | Secondary |
| `Approval_Action_Edit` | `Edit input` | Secondary |
| `RunLog_Filter_All` | `All agents` | P6 tabs |
| `ComposeDrawer_Compare` | `Compare architectures` | P7 drawer trigger |
| `TeamPanel_Regenerate` | `Regenerate as…` | P7 menu |

**Existing strings to leave alone:** `Plan_Title` (`"Proposed Execution
Plan"`), `Plan_Section_Label` (`"Execution plan"`), anything else under
the `Plan_*` prefix.

---

## 4 · Data contracts (summary)

All additions below ship on the **plan payload** (camelCase on the wire,
snake_case in the Python model). Keep `plan.py` and `plan/types.ts` in
lock-step.

| Field | Type | Phase | Backend? | Notes |
|---|---|---|---|---|
| `team.pattern` | `"supervisor" \| "sequential" \| "network" \| "solo" \| "mixed"` | P3 | **yes** | Chosen by orchestrator at plan time. |
| `team.nodes[]` | `{id, agent, role, status}` | P3 | **yes** | `status` updates over the run lifecycle via existing progress events. |
| `team.edges[]` | `{from, to, kind}` | P3 | **yes** | `kind` ∈ `delegate` / `peer` / `report`. |
| `step.blastRadius` | enum | P4 | **yes** | Orchestrator classifies per step. |
| `step.toolCallPreview` | `{name, args}` | P4 | **yes** | Redacted; secrets stripped. |
| `step.recoveryActions[]` | enum[] | P4 | **yes** | Which of the four actions this step supports. |
| `phase.ownerAgent` | `string \| null` | P6 | **yes** | Agent id that produced this phase entry. |

New runtime events (WebSocket / poll):

| Event | Phase | Notes |
|---|---|---|
| `team.node.statusChanged` | P3 | `{nodeId, status}` — drives the graph ring and strip chip. |
| `approval.requested` | P4 | Switches the right pane into `ApprovalCard`. |
| `approval.resolved` | P4 | Dismisses the card; resumes the run. |

---

## 5 · Verification

Each phase maps to a prototype screen. Eyeball comparisons:

| Phase | Prototype screen | Live page |
|---|---|---|
| P1 | `index.html` screen 1 (composer CTA) | `/agent-hub/orchestrator` |
| P2 | `index.html` screens 3–6 (sticky task recap bar) | `/agent-hub/session/:id` |
| P3 | `index.html` screens 3–5 (team panel collapsed) + `architectures.html` (expanded, per-pattern) | `/agent-hub/session/:id` |
| P4 | `index.html` screen 4 (approval card) | `/agent-hub/session/:id` (during run) |
| P5 | `index.html` screens 2 → 3 transition | `/agent-hub/orchestrator` → in-place → run |
| P6 | `index.html` screen 6 (filterable log tabs) | `/agent-hub/session/:id?status=completed` |
| P7 | `architectures.html` (all five patterns, "Compare architectures" drawer) | `/agent-hub/orchestrator` plan step |

Add Playwright coverage under `Frontend/e2e/` for each phase:
`Frontend/e2e/plan.spec.ts` already exists — pattern new specs after it.

---

## 6 · Things we deliberately did NOT take from the prototype

The prototype has shortcuts that should **not** ship as-is:

- **Tailwind utility classes.** Live workload uses Fluent + SCSS. Port
  the prototype's visual language into Fluent tokens + existing SCSS
  variables; don't add Tailwind.
- **Prototype-only chrome:** bottom page counter, "Prototype" pill,
  6-screen split, `nav.js`, the black footer banner. All review-only.
- **Hard-coded animation timings.** Use the existing
  `prefers-reduced-motion: reduce` guard that the live workload
  already honours.
- **Inline SVG positions.** The prototype hard-codes `x=30, y=180` etc.
  In the live product, node coordinates are computed from
  `team.nodes.length` and `team.pattern`. See `OrchCanvas.tsx` plan.
- **`auto_awesome` Material Symbol.** The live workload uses Fluent
  icons (`Sparkle24Regular`). Keep it.

---

## 7 · When in doubt

- The prototype screens are at
  `Design/agent-ux/prototypes/03-mission-control/index.html` (live flow)
  and `…/architectures.html` (pattern comparison).
- `README.md` in the same folder explains the *why* behind each change.
- Ask before touching anything on the "do-not-touch list" in §0.
- Ask before introducing a new npm dependency.
