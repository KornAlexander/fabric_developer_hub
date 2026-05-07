---
description: "Overhaul the Fabric ClawHub codebase architecture while preserving behavior and validating thoroughly"
name: "Fabric ClawHub Codebase Overhaul"
argument-hint: "Optional focus area, such as Backend, Frontend, MCP tools, or AgentHub orchestration"
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
- MCP servers: `Developer Hub/Backend/src/mcp_servers`
- MCP registry: `Developer Hub/Backend/src/mcp_servers.json`
- AgentHub backend services: `Developer Hub/Backend/src/services/agenthub`
- Tool policy/runtime: `Developer Hub/Backend/src/services/agenthub/tool_policies.py` and `Developer Hub/Backend/src/services/agenthub/tool_runtime.py`
- Orchestrator: `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`
- Agent/catalog configuration: `Developer Hub/Backend/src/services/agenthub/catalog.yaml`
- Frontend: `Developer Hub/Frontend`
- Frontend source: `Developer Hub/Frontend/src`
- Frontend package/scripts: `Developer Hub/Frontend/package.json`
- Frontend webpack/test config: `Developer Hub/Frontend/tools`, `Developer Hub/Frontend/tsconfig.json`, `Developer Hub/Frontend/vitest.config.ts`, `Developer Hub/Frontend/playwright.config.ts`
- Frontend root app/entry/style files: `Developer Hub/Frontend/src/App.tsx`, `Developer Hub/Frontend/src/index.ts`, `Developer Hub/Frontend/src/index.ui.tsx`, `Developer Hub/Frontend/src/theme.tsx`, `Developer Hub/Frontend/src/styles.scss`
- Frontend AgentHub UI: `Developer Hub/Frontend/src/components/AgentHub`
- Frontend AgentHub layout/pages: `Developer Hub/Frontend/src/components/AgentHub/AgentHubLayout.tsx`, `DashboardPage.tsx`, `AgentsPage.tsx`, `OrchestratorPage.tsx`, `SettingsPage.tsx`
- Frontend new-session/composer context: `Developer Hub/Frontend/src/components/AgentHub/RichComposer.tsx`, `MentionPicker.tsx`, `ItemContext.tsx`, `SearchContext.tsx`, `WorkspacePreviewModal.tsx`, `PdfPreview.tsx`, `TaskPromptRecap.tsx`, `Step2View.tsx`
- Frontend mission control: `Developer Hub/Frontend/src/components/AgentHub/mission`
- Frontend editor tabs: `Developer Hub/Frontend/src/components/AgentHub/EditorTabs`
- Frontend plan/team/approval UI: `Developer Hub/Frontend/src/components/AgentHub/plan`, `team`, `approvals`
- Frontend tests: `Developer Hub/Frontend/tests` and `Developer Hub/Frontend/e2e`
- Design/reference docs and screenshots: `Developer Hub/docs` and top-level `Design`

Context:

This project has been iterated over many sessions. A lot of code was added quickly on top of older code. There are likely leftovers, duplicated concepts, dead code, old experimental paths, inconsistent architecture, partially replaced implementations, stale docs, unused tests or artifacts, and places where responsibilities are mixed together. The goal is a serious architectural cleanup and refactor while preserving functionality.

Your assignment:

Overhaul the codebase architecture and structure so it is cleaner, more maintainable, and closer to software engineering best practices, while keeping existing user-visible functionality intact.

Also perform a comprehensive bug and security sweep. Inventory the repository files and product capabilities, then check all real source, configuration, test, script, prompt, and documentation files for defects, unsafe assumptions, stale behavior, and security risks. Generated outputs, binary screenshots, caches, dependency folders, and test-result artifacts may be classified as generated/non-source instead of manually reviewed, but they should still be accounted for rather than silently ignored.

Do not merely make cosmetic changes. Identify and remove dead code, consolidate duplicated concepts, simplify modules that grew too large, improve subsystem boundaries, and make the implementation easier to reason about. Find and fix bugs and security issues as part of the overhaul. Keep the product behavior working.

Important product behavior to preserve:

- AgentHub sessions and orchestration must keep working.
- The orchestrator/generalist must remain internal and must not appear as a normal public frontend agent.
- Existing MCP tools and policy enforcement must keep working.
- The Fabric definition workspace workflow must keep working:
  - checkout
  - list files
  - diff
  - validate
  - plan publish
  - publish only with proper write gating or confirmation
  - discard only with proper write gating or confirmation
- Shell, web, code-interpreter, Azure, and Fabric tooling should remain registered and governed by policies.
- Frontend routes, navigation, sidebar behavior, page preloading, global search, and editor tabs should remain functional.
- Frontend session/dashboard behavior should remain functional, including status counts, active/recent session lists, route helpers, and cancel/continue interactions.
- Frontend new-session behavior should remain functional, including the rich composer, @mentions, workspace/item context, attachments, previews, validation messages, and direct-start flow.
- Frontend mission control should remain functional, including live stream rendering, log visibility tiers, readable public log text, approval cards, team visibility, and hiding internal orchestrator nodes.
- Frontend should continue using existing design-system conventions unless a cleanup directly requires a UI change. Do not redesign screens as part of architecture cleanup unless explicitly necessary.
- Existing backend API route behavior must remain compatible unless there is a deliberate documented migration.

Initial workflow:

1. First inspect the current repository state. Do not assume this prompt is fully up to date.
2. Check git status and identify which changes are already present. Do not revert user changes.
3. Check whether the local services are running before doing browser/e2e validation or manual product verification. If the containers are not running, start them from `Developer Hub` with:

```bash
cd "Developer Hub"
./start.sh dev
```

Use dev mode for active development because it starts the frontend dev server with HMR. The script also brings up the backend, manifest/dev-gateway services, and required Docker Compose profiles. If startup fails, inspect Docker output and fix the root cause before continuing with UI/e2e verification.

4. Build a repository file inventory before editing. Classify files into source, config, tests, scripts, docs/prompts, generated artifacts, screenshots, caches, and external/dependency outputs. Use that inventory to make sure all meaningful files are considered during cleanup, bug review, and security review.
5. Build a functionality inventory before editing. Cover backend APIs, MCP tools, orchestration/session flows, frontend routes/pages, composer/mention/attachment workflows, mission/live-log workflows, editor tabs, authentication, service startup, and validation/e2e workflows.
6. Read the key backend and frontend architecture files before editing:
   - `Developer Hub/Backend/src/mcp_servers.json`
   - `Developer Hub/Backend/src/services/agenthub/tool_policies.py`
   - `Developer Hub/Backend/src/services/agenthub/tool_runtime.py`
   - `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`
   - `Developer Hub/Backend/src/services/agenthub/catalog.yaml`
   - `Developer Hub/Backend/src/mcp_servers/fabric_definition_workspace.py`
  - `Developer Hub/Frontend/package.json`
  - `Developer Hub/Frontend/src/App.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/AgentHubLayout.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/DashboardPage.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/AgentsPage.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/OrchestratorPage.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/mission/MissionControlPage.tsx`
  - `Developer Hub/Frontend/src/components/AgentHub/mission/useMissionStream.ts`
  - `Developer Hub/Frontend/src/components/AgentHub/mission/missionReducer.ts`
  - `Developer Hub/Frontend/src/components/AgentHub/mission/logPresentation.ts`
  - `Developer Hub/Frontend/src/components/AgentHub/mission/logVisibility.ts`
  - `Developer Hub/Frontend/src/components/AgentHub/team/teamVisibility.ts`
  - `Developer Hub/Frontend/src/components/AgentHub/EditorTabs/EditorTabsContext.tsx`
7. Build a short architecture map of what exists now:
   - major backend subsystems
   - major frontend subsystems
   - MCP and tooling boundaries
   - orchestration and session boundaries
  - frontend route/page boundaries
  - frontend event/state boundaries, especially mission stream, composer state, dashboard state, editor tabs, and global search
  - frontend/backend API contracts used by AgentHub screens
  - cross-cutting bug and security risk areas
   - test coverage and validation commands
8. Identify concrete cleanup, bug-fix, and security-hardening opportunities:
   - dead code
   - duplicate code paths
   - stale or superseded files
   - overly large modules
   - unclear ownership boundaries
   - unsafe or over-permissive policies
   - tests that no longer describe current behavior
  - incorrect edge-case handling, race conditions, stale route behavior, broken error handling, and missing validation
  - authentication, authorization, token handling, path traversal, SSRF, command injection, unsafe shell/web tool behavior, data leakage, XSS, unsafe URL handling, insecure CORS, and secret exposure risks
  - frontend duplicate state models, route helpers, formatting helpers, ad hoc event parsing, and UI logic embedded in large page components
  - stale frontend backup files, generated screenshots, or test-result artifacts only when they are clearly unreferenced and safe to remove
9. Then implement the cleanup in safe phases. Do not stop at a plan.

Bug and security sweep expectations:

- Treat every meaningful file as in scope: backend Python, frontend TypeScript/React/Sass, MCP server definitions, Docker/Compose/startup scripts, tests, docs, prompts, config, YAML/JSON, and validation scripts.
- For files that are generated, binary, cached, vendored, or test-output artifacts, explicitly classify them and decide whether they should be removed, ignored, regenerated, or left alone.
- Exercise every major product capability through tests, local commands, or browser/e2e verification where practical. If a capability cannot be exercised because of auth or external service constraints, document the exact blocker and run the closest local validation.
- Fix bugs found during review instead of only listing them. Add or update regression tests when a bug fix touches executable behavior.
- Fix security issues found during review. Pay special attention to auth boundaries, token storage/logging, environment files, local shell execution, web fetch/search restrictions, MCP tool policies, Fabric API calls, path handling, file writes/deletes, user-provided URLs, uploaded/attached files, SSE/live-log parsing, frontend rendering of untrusted text, external links, Docker scripts, and CI/test scripts.
- Never print secrets, tokens, private env values, or captured auth material in logs, tests, docs, prompts, or final summaries.
- If a risky behavior is intentionally allowed, document the guardrail that makes it acceptable and add tests around that guardrail where possible.

Frontend architecture goals:

- Treat the frontend as a first-class part of the overhaul, not as an afterthought.
- Clarify boundaries between routing/layout, page-level orchestration, reusable UI components, API/data adapters, and pure presentation helpers.
- Keep route ownership obvious: layout/navigation in `AgentHubLayout`, session overview logic in `DashboardPage`, new-session composition in `OrchestratorPage` and composer modules, live execution in `mission`, and tab workspace behavior in `EditorTabs`.
- Prefer pure helper modules for formatting, visibility, count derivation, routing helpers, and event normalization when this reduces page/component complexity.
- Keep mission stream parsing and reducer behavior deterministic and covered by focused tests.
- Keep internal orchestrator filtering centralized so it cannot accidentally reappear in team diagrams, agent lists, or public UI surfaces.
- Keep frontend types aligned with backend API payloads. If API shapes are changed, update backend serializers, frontend types, and tests together.
- Keep UI changes restrained. Refactor structure and remove dead code without changing visual design unless the current design is broken or duplicated code can only be cleaned safely with a small UI adjustment.
- Preserve accessibility and interaction behavior for dialogs, buttons, tabs, side navigation, composer inputs, mention picker, attachment previews, approval controls, and mission log controls.
- Remove stale `.bak`, abandoned experimental components, generated artifacts, or unused CSS only after proving no imports, tests, docs, or runtime paths depend on them.

Refactoring rules:

- Preserve behavior first.
- Prefer small, verifiable phases over a giant untested rewrite.
- Do not delete code just because it looks old; prove it is unused by search, imports, route references, tests, or runtime wiring.
- Remove dead code when evidence is strong.
- Consolidate duplicate logic into existing local patterns.
- Keep public API compatibility unless changing it is clearly necessary, then update all callers and tests.
- Keep MCP tool names stable unless you update registry, policies, catalog, tests, and callers together.
- Keep security boundaries strict:
  - read tools may be auto-allowed when appropriate
  - write and destructive tools should require confirmation unless there is a deliberate first-party orchestration reason
  - never make publish, delete, or destructive operations easier to call accidentally
- Do not introduce broad new frameworks unless the existing architecture cannot support the cleanup.
- Avoid unrelated UI redesign unless frontend architecture cleanup requires touching UI code.
- Do not commit changes unless explicitly asked.
- Do not revert unrelated work in the dirty worktree.

Backend validation expectations:

From `Developer Hub/Backend`, use the project venv:

```bash
.venv/bin/python -m ruff check <changed backend files>
.venv/bin/python -m json.tool src/mcp_servers.json
.venv/bin/python -m pytest --no-cov <focused affected backend tests>
```

Known useful backend focused tests include:

```bash
.venv/bin/python -m pytest --no-cov \
  tests/unit/mcp_servers/test_fabric_definition_workspace.py \
  tests/unit/services/agenthub/test_tool_policies.py \
  tests/unit/api/test_agenthub_controller_routes.py \
  tests/unit/services/agenthub/test_tool_runtime.py
```

If touching orchestration or session infrastructure, also run relevant tests such as:

```bash
.venv/bin/python -m pytest --no-cov \
  tests/unit/api/test_agenthub_controller_routes.py \
  tests/unit/api/test_agenthub_controller_security.py \
  tests/unit/services/agenthub/test_session_store.py \
  tests/unit/services/agenthub/test_workspace_context_service.py \
  tests/unit/services/agenthub/test_workspaces_cache.py \
  tests/unit/services/agenthub/drivers/test_maf_sequential.py \
  tests/unit/services/test_correlation.py \
  tests/unit/services/test_opentelemetry_setup.py
```

Frontend validation expectations:

From `Developer Hub/Frontend`, inspect `package.json` first and use the repo's existing scripts. For changed frontend code, run the relevant typecheck, build, unit test, and focused Playwright commands. This repo may not have a dedicated lint script; do not invent one without checking `package.json`.

Useful frontend validation commands include:

```bash
npx tsc -p tsconfig.json --noEmit
npm run build:test
npm test -- --run
```

For focused frontend tests, prefer the smallest relevant Vitest slice first, then broaden when changing shared helpers. Known useful focused tests include:

```bash
npm test -- --run \
  tests/mission/logPresentation.test.ts \
  tests/mission/logVisibility.test.ts \
  tests/sessions/dashboardPage.logic.test.ts \
  tests/teamVisibility.test.ts \
  tests/EditorTabsReducer.test.ts
```

For frontend end-to-end or visual behavior, use the existing Playwright specs that match the changed area, for example:

```bash
npm run test:e2e:new-session
npm run test:e2e -- e2e/orchestrator-internal.spec.ts --project=chromium
npm run test:e2e -- e2e/mission-control-reference-visual.spec.ts --project=chromium
npm run test:e2e -- e2e/mission-control-redesign.spec.ts --project=chromium
```

If UI behavior, layout, live mission logs, or visual styling changes, verify with Playwright screenshots or snapshots, not just unit tests. If browser authentication or external Fabric/Power BI access blocks an e2e proof, document the exact blocker and still run the local focused tests and build/type checks.

Testing loop requirement:

Run validation after each meaningful phase. If validation fails, fix the root cause and rerun. Continue this loop until:

- focused tests pass
- lint and type checks pass for changed areas
- JSON, YAML, and config files parse
- editor diagnostics are clean for touched files
- removed files are confirmed unused
- behavior-preserving changes are covered by tests
- bugs fixed during the sweep have regression coverage or a documented verification path
- security fixes have explicit validation, tests, or documented guardrail checks
- all major functionality is smoke-tested, covered by focused tests, or documented with a concrete blocker and closest available validation
- any remaining risks are documented clearly

Definition of done:

The work is done only when you can provide:

1. A concise summary of architecture improvements made.
2. A list of dead or obsolete code removed and why it was safe.
3. A list of bugs found and fixed, including how each important fix was verified.
4. A list of security issues found and fixed, including the guardrails or tests added.
5. A list of files or modules significantly refactored.
6. A summary of the file/functionality inventory coverage, including any generated or excluded files and why they were excluded.
7. The validation commands run and their results.
8. Any remaining known risks or follow-up items.

Start now by inspecting the repository and current git state, then proceed through implementation and validation autonomously until the cleanup is complete or you hit a genuine blocker.
