# PBI Fixer — Changelog

> Implementation history for shipped workstreams. Forward-looking work lives in `PLAN.md`.

---

## Phase 1 — Initial port (v0.1.0, April 2026)

Migrated from Python (ipywidgets) to TypeScript (React + FluentUI v9).
Source: `c:\Users\alkorn\repos\pbi_fixer\src\` (~12,000+ lines, 60+ files).

| Component | Python Source | TS File(s) | Lines |
|---|---|---|---|
| Types — Model | `_sm_explorer.py` | `types/model.ts` | 92 |
| Types — Report | `_report_explorer.py` | `types/report.ts` | 40 |
| Types — Common | `_ui_components.py` | `types/common.ts` | ~30 |
| Theme | `_ui_components.py` | `utils/theme.ts` | 35 |
| Tree Utilities | `_ui_components.py` | `utils/treeUtils.ts` | 75 |
| Model Tree Builder | `_sm_explorer.py` | `utils/modelTree.ts` | 200 |
| Report Tree Builder | `_report_explorer.py` | `utils/reportTree.ts` | 160 |
| Fabric API Service | `sempy_labs` / `sempy.fabric` | `services/fabricApi.ts` | 420 |
| Model Explorer | `_sm_explorer.py` (850) | `components/ModelExplorer.tsx` | 500 |
| Report Explorer | `_report_explorer.py` (700) | `components/ReportExplorer.tsx` | 570 |
| PBI Fixer (Orchestrator) | `_pbi_fixer.py` (100) | `components/PbiFixer.tsx` | 150 |

Total: ~2,280 lines TS across 16 files (from ~1,750 lines Python).

---

## WS-A — Shell / nav / theming (v0.1–v0.5)
Flat left-side nav tree replacing the top TabList. Connection bar with workspace folder grouping. Stub pages under "Others". Dev Hub theme alignment.

## WS-B — Memory Analyzer (v0.18, Phase 2 first)
- Phase 2 shipped first because backend image still lacks `semantic-link-labs`/`semantic-link` (verified against `Backend/pyproject.toml`). Same precedent as WS-C/D.
- Files: `services/memoryApi.ts`, `pages/MemoryPage.tsx`, all via existing `executeDax` proxy.
- Working tabs (via `INFO.VIEW.*` DAX): Tables, Columns, Measures, Relationships. Summary cards, sort/filter/CSV export per tab.
- Deferred to Phase 1 (backend bridge), surfaced via info `MessageBar`: per-column `TotalSize` / `DictionarySize` / `DataSize` / `Cardinality` / `Encoding` / `Segments`, Partitions, Hierarchies. Reason: Power BI `executeQueries` REST rejects raw `INFO.STORAGETABLE*`, `INFO.PARTITIONS()`, `INFO.HIERARCHIES()` with `DatasetExecuteQueriesError` / `AnalysisServicesErrorCode 3239575574`. Only `INFO.VIEW.*` family allowed; `sempy_labs.vertipaq_analyzer()` works because it goes via XMLA.
- Nav consolidation: `vertipaq` nav key removed; single "Memory" entry (💾) replaces both stubs. `Others` count 12 → 11.
- Phase 1 follow-up plan: add `semantic-link-labs` to `Backend/pyproject.toml`, expose `POST /api/pbi-fixer/vertipaq {workspaceId, datasetId}` returning the same shape, swap `loadVertipaqData` internals to a single `pbiFixerProxy("workload", "vertipaq", body)` call.
- Verified live (Playwright, Demo workspace / Bad Report - Testing): all 4 data tabs render; INFO.VIEW.* returns 1 table + 9 columns + 1 measure + 0 relationships.

## WS-C — Model BPA (v0.12)
- sempy-labs backend bridge deferred until Python dep lands.
- Ships client-side rule engine (`services/modelBpaApi.ts`) on already-loaded `ModelData`. Endpoint shape and grid layout match the eventual backend contract → zero-touch swap.
- 10 rules across DAX, Model, Perf, Naming categories.
- "Fix it" dispatches `pbifixer:bpa-fix` CustomEvent for WS-N to consume.

## WS-D — Report BPA (v0.13)
- Same hybrid pattern as WS-C: client-side rule engine (`services/reportBpaApi.ts`) on loaded `ReportData`.
- 10 rules across Visualization, Layout, Accessibility, Performance.
- Same `pbifixer:bpa-fix` CustomEvent contract as WS-C → WS-N wires both the same way.

## WS-E — Fixer Execution (v0.14 → backend apply v0.41)

### v0.14 — UX shipped, write-back stubbed
- Full UX contract: checkbox list grouped by scope, Scan/Apply mega-button, Apply switch + diff preview + confirm dialog, live log panel, BPA "Fix it" preselection via `pbifixer:bpa-fix` listener.
- 6 fixer `scan` implementations (Pie, Bar, Column, PageSize, UpgradeToPbir, DiscourageImplicitMeasures).
- Verified on Bad Report - Testing: Pie 1 / Bar 8 / Column 6 / PageSize 0 / UpgradeToPbir 0 / DiscourageImplicitMeasures 0.

### v0.41 — Write-back wired
- New backend endpoint `POST /api/pbi-fixer/fixers/apply` mirrors v0.40 translations LRO pattern (`getDefinition?format=TMDL` → in-memory mutate → `updateDefinition`).
- New module `Backend/src/services/agenthub/pbi_fixer_handlers.py` with `FIXER_HANDLERS` registry — **13 handlers** (8 SM + 5 Report).
- Frontend `fixers/index.ts` rewritten as thin backend-delegating registry (`backendFixer({id, title, scope})`).
- Only `Fix_UpgradeToPbir` remains a stub (needs sempy-labs runtime).
- Pytest at `Backend/tests/unit/services/agenthub/test_pbi_fixer_handlers.py` (15 cases).
- Verified end-to-end via Playwright: Fix_PieChart 1 finding, Fix_FloatingPointDataType 3 findings, both `applied=true · written back to Fabric`.

Shipped fixers (v0.41):
- **SM**: Fix_FloatingPointDataType, Fix_DoNotSummarize, Fix_DiscourageImplicitMeasures (alias), Fix_IsAvailableInMdxFalse, Fix_MeasureFormat, Fix_PercentageFormat, Fix_WholeNumberFormat, Fix_HideForeignKeys
- **Report**: Fix_PieChart, Fix_PageSize (1280×720), Fix_HideVisualFilters, Fix_DisableShowItemsNoData, Fix_RemoveUnusedCustomVisuals
- **Stub**: Fix_UpgradeToPbir

### v0.49 — P1 SM fixers batch (May 2026)
Three more semantic-model fixers ported from `pbi_fixer/src/`, all using the existing TMDL round-trip pattern (no sidecar required — these don't need calculated tables / calc groups / relationships, so they fit `pbi_fixer_handlers.py`).

- **Fix_AvoidAdding0** — strip leading `0+` (or `0 + ` with any spacing) from measure DAX expressions. Handles both inline (`measure 'X' = 0 + COUNTROWS(Foo)`) and block-form (`measure 'X' =\n\t0 + COUNTROWS(Foo)`) measure definitions.
- **Add_LastRefreshTable** — append a hidden `Last Refresh` table with one M-partition column (`Last Refreshes`) wrapping `DateTime.LocalNow()`, plus a `Last Refresh Measure` that surfaces the timestamp. Skips creation if any existing table name contains `refresh` (case-insensitive). If a table whose name contains `measure` exists, the measure is appended there; otherwise it sits on the new table.
- **Add_MeasuresFromColumns** — for every column whose `summarizeBy` is `sum` / `count` / `min` / `max` / `average` / `distinctCount`, create a measure `<ColName> = AGG('Table'[Col])` and hide the source column. Auto-detects a measure-host table by name; otherwise the measure is appended to the source table. Skips columns where a measure of the same name already exists.

Frontend `fixers/index.ts` adds three new `backendFixer({...})` entries; backend `pbi_fixer_handlers.py` adds three new handlers + registry rows. No new API endpoints — all three reuse the v0.41 `POST /api/pbi-fixer/fixers/apply` dispatcher.

## WS-F — Perspectives (v0.15)
- UX: matrix grid with tri-state checkboxes, add / rename / delete perspective, dirty-change tracker, Apply switch + confirmation dialog.
- Reads from TMDL semantic-model definition (`getSemanticModelDefinition`) — more robust than `INFO.PERSPECTIVES()` DAX (requires newer compat level, returned 400 on demo model).
- Parser walks `perspective → perspectiveTable → perspectiveColumn|Measure|Hierarchy` and emits `PerspectiveMember` rows with stable paths (`Table`, `Table[Column]`, `Table::Hierarchy`).
- TOM write-back stubbed pending sempy-labs backend bridge: `applyPerspectiveChanges` surfaces "Backend bridge not yet wired — Apply deferred."
- Dedicated `perspectives_controller.py` lands with the write-back work (separate workstream from WS-E).

## WS-G — Translations + Auto-Translate (v0.11 → backend apply v0.40)

### v0.11 — UX shipped
- Workflow: language + scope picker, Generate proposal, inline-editable review grid, Accept/Reject per row + bulk, Apply confirmation dialog with diff counts, JSON/CSV export.
- Backend `translations/propose` + `translations/apply` returned 501 until LLM + sempy-labs bridge.

### v0.17 — 405 fix on Generate proposal
- Root cause in `services/translationsApi.ts`: `BE` base URL was guarded with `typeof process !== "undefined" && process.env && process.env.WORKLOAD_BE_URL` — browser bundle short-circuits to `""`, POST went relative to iframe origin (`http://127.0.0.1:60006`) where nginx only serves GET routes.
- Fix: dropped the guard to match `controller/AgentHubApi.ts` (plain `process.env.WORKLOAD_BE_URL || ""`, webpack DefinePlugin rewrites at build).
- Added 501-branch to `proposeTranslations` so banner renders instead of raw error.
- Verified end-to-end against Demo / Bad Report - Testing — 52 proposals rendered, no 405.

### v0.40 — Backend write-back shipped
- New endpoint `pbi_fixer_translations_apply` + module `Backend/src/services/agenthub/tmdl_translations.py` (parse/serialize/merge culture TMDL, deterministic output, preserves `linguisticMetadata` block verbatim, escapes special-char names with single quotes).
- Flow: OBO Fabric token → `getDefinition?format=TMDL` (LRO) → find/create `definition/cultures/<culture>.tmdl` → merge ApplyItems → `updateDefinition` (LRO).
- Frontend stripped of all 501 branches; confirm-dialog body describes the TMDL round-trip.
- Pytest at `Backend/tests/unit/services/agenthub/test_tmdl_translations.py` (5 cases).
- **Same TMDL round-trip pattern reused for v0.41 Fixer apply.**

## WS-I — Delta Analyzer (v0.24)
- Same hybrid pattern as WS-B/C/D — full UX without waiting on sempy-labs bridge.
- Snapshots captured via `INFO.VIEW.*` DAX (reuses `loadVertipaqData` from `memoryApi.ts`); stored in `sessionStorage` (capped at 20, FIFO eviction on quota).
- Diff engine in `services/deltaApi.ts` indexes Tables / Columns / Measures / Relationships by stable key, emits Added / Removed / Changed rows with per-property before→after diffs.
- Page renders summary cards (`+a −r ~c` per category) plus tabbed grid; "Live model" compare side captures on-demand against currently-loaded SM. CSV export.
- Eventual `pbi_delta_controller.py` backend bridge will be drop-in replacement for `takeSnapshot` — diff engine and UI untouched.

## WS-J — Diagram (v0.19 → hotfix v0.20)
- Library choice: `reactflow` was proposed but installing required `npm install` inside frontend image (no `node_modules` on host) and webpack config touch — both contrary to WS-J/WS-N split. Shipped **pure SVG canvas** instead → `Frontend/package.json` unchanged, identical UX.
- Files: `services/diagramApi.ts` (data flattening + sessionStorage layout cache + auto-layout grid seeded by relationship degree), `components/pages/DiagramPage.tsx` (toolbar + SVG canvas + custom node/edge renderers).
- Rendering:
  - Card per table (header bar = drag handle, body lists visible columns truncated at 12 with "+ N more…" footer; calculation groups/tables tinted differently; key columns flagged with 🔑; hidden tables auto-filtered with `Show hidden` toggle).
  - Edge per relationship with anchor points snapped to closest card border. Cardinality (`1` / `*`) glyphs on each endpoint, mid-line filter-direction diamond (◇ single, ◈ both), dashed + dimmed when `isActive=false`.
- Interaction: pointer-down on header drags card (layout persisted to `sessionStorage` keyed by datasetId). Pointer-down on canvas pans. Mouse wheel zooms (cursor-anchored). `Fit` recenters to bounding box. `Reset Layout` clears stored positions and re-runs auto-layout. `Refresh` reloads model. Collapse ▴/▾ toggles column body to header-only (Playwright-verified: text count drops 20 → 2).
- **v0.20 hotfix**: v0.19 imported `Layout20Regular` and `ArrowMaximize20Regular` from `@fluentui/react-icons` — both undefined exports in bundled version, threw React minified `#130` ("got: undefined"), left canvas blank. Fixed by switching to `ArrowExpand20Regular` for `Fit` and removing icon on `Reset Layout`.
- Verified Playwright (Demo / Bad Report - Testing): 1 card (Orders) with 9 visible columns, type badges, legend, full toolbar.
- Known gap vs spec: zoom/pan is local state. Layout per-tab via sessionStorage. Future: orthogonal edge routing instead of straight lines.

## WS-M — Prototype (v0.16)
- Full UX: 8-type visual palette (Card / Table / Matrix / Bar / Column / Line / Pie / Slicer), free-positioned canvas with 40 px grid background, click-to-add visuals, mouse drag to move, bottom-right resize handle, Inspector panel for title + field binding from loaded SM (`loadModelData`).
- Export emits PBIR-lite JSON skeleton (`pbir-skeleton/1.0`) downloaded via `Blob` + `<a download>`.
- Upload-as-report surfaces stubbed backend bridge message — real PBIR `createReport` + content upload lands once sempy-labs bridge is wired.
- Fields carry `role` (Values / Category) and `kind` (column / measure) so eventual converter maps into PBIR query roles without re-inferring.

## WS-N — Integration & Shared Assets Sweep (v0.36–v0.37)
- BPA "Fix it" → Fixer page wiring shipped incrementally inside WS-C/D/E (`pbifixer:bpa-fix` CustomEvent + `onNavigate('fixer')`).
- Cross-workstream smoke passes ran after each WS merge (manual Playwright).
- v0.36: `utils/version.ts` extracted as single source of truth (re-exported from `utils/index.ts`); `PbiFixerPage.tsx` imports from there.
- v0.36: Cross-tab BPA Fix-it relay — shell-level listener in `AgentHubLayout.tsx` catches event from any sub-tab, stashes payload in `sessionStorage["pbiFixer.pendingBpaFix"]`, opens Fixer tab via `handlePbiFixerSubNav("fixer")`, then re-dispatches with `__relayed=true`. `FixerPage.tsx` drains sessionStorage on mount + listens for relay.
- v0.37: Removed stale "WS-N" mis-labels from `TranslationsPage.tsx` and `modelBpaApi.ts` — backend TOM-write apply path is a separate future workstream, not WS-N.
- Pending: shared frontend deps for Monaco (WS-K). `reactflow` no longer needed (WS-J shipped without it).

## WS-Q — Editable visual / page properties (v0.42)
- Editable type / position / size + preview overlap fix.
- Files: `ReportExplorer.tsx`, `services/fixersApi.ts`, backend `agenthub_controller.py` `/pbi-fixer/visual/update`.
