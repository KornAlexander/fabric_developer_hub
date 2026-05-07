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

### v0.50 — P1 fixer batch (May 2026)
Six P1 fixers ported from `pbi_fixer/src/` in one shipping batch — three semantic-model, three report. All reuse the v0.41 round-trip dispatcher (no sidecar required).

**Semantic model (TMDL):**
- **Fix_AvoidAdding0** — strip leading `0+` (any spacing) from measure DAX. Handles inline (`measure 'X' = 0 + COUNTROWS(Foo)`) and block-form measures.
- **Add_LastRefreshTable** — append a hidden `Last Refresh` table with one M-partition column wrapping `DateTime.LocalNow()`, plus a `Last Refresh Measure` that surfaces the timestamp. Skips creation if any existing table name contains `refresh` (case-insensitive). If a table whose name contains `measure` exists, the measure is appended there.
- **Add_MeasuresFromColumns** — for every column whose `summarizeBy` ∈ {sum, count, min, max, average, distinctCount}, create a measure `<ColName> = AGG('Table'[Col])` and hide the source column. Auto-routes to the first table whose name contains `measure`; otherwise the measure is appended to the source table. Skips columns where a measure of the same name already exists.

**Report (PBIR):**
- **Fix_BarChart** — best-practice formatting on `barChart` / `clusteredBarChart`: removes X-axis title + values, Y-axis title and vertical gridlines; turns on data labels.
- **Fix_ColumnChart** — best-practice formatting on `columnChart` / `clusteredColumnChart`: removes X-axis title, Y-axis title + values, vertical gridlines; turns on data labels.
- **Fix_VisualAlignment** — within each page, group visible chart visuals whose width / height / X / Y differ by ≤ 2 % of the page dimension and snap them to the first visual in the group (sorted-anchor scan). Mirrors `_Fix_VisualAlignment.py`.

Frontend `fixers/index.ts` adds six new `backendFixer({...})` entries; backend `pbi_fixer_handlers.py` adds six new handlers + registry rows. No new API endpoints — all reuse the v0.41 `POST /api/pbi-fixer/fixers/apply` dispatcher.

### v0.51 — P2 SM fixer batch (May 2026)
Six P2 semantic-model fixers ported from `pbi_fixer/src/`. All are property-mutation fixers using the existing TMDL helpers (no relationship rename, no name changes). Sempy-labs-only fixers (`Fix_UpgradeToPbir`) and large workstreams (`Add_PrepForAI`, ~500 lines) deferred. Object-rename fixers (`Fix_TrimObjectNames`, `Fix_CapitalizeObjectNames`) deferred — they require updating every TMDL reference and all PBIR visual bindings.

**Semantic model (TMDL):**
- **Fix_DateColumnFormat** — set `formatString: "mm/dd/yyyy"` on columns named exactly `Date` that have no `formatString`.
- **Fix_DataCategory** — set `dataCategory` on columns whose names match well-known geo / URL / image patterns (City, Country, State/Province, PostalCode, Continent, Latitude, Longitude, WebUrl, ImageUrl, Address, County). Skips columns that already have a non-Uncategorized data category.
- **Fix_MarkPrimaryKeys** — for every relationship's `to` column, set `isKey: true` on that column when the table currently has no key column. Marks at most one column per table.
- **Fix_MeasureDescriptions** — for every visible measure with no description and an inline DAX expression, set `description` to the expression text. Block-form measures are conservatively skipped.
- **Fix_UseDivideFunction** — rewrite simple `[A] / [B]` and `(...) / (...)` patterns inside inline measure expressions to `DIVIDE([A], [B])`. Skips measures already dominated by `DIVIDE`.
- **Fix_DefaultDataSourceVersion** — set `defaultPowerBIDataSourceVersion: PowerBI_V3` on the `model` block in `definition/model.tmdl` (required for XMLA write on Fabric / Premium capacities).

Frontend adds six `backendFixer({...})` entries; backend `pbi_fixer_handlers.py` adds six handlers + registry rows. No new API endpoints.

### v0.82 — WS-U done: SLL Python sidecar deleted (May 2026)
The standalone `sll-sidecar` Python container is gone for good. Memory Analyzer keeps using its DAX `INFO.VIEW.*` path via `memoryApi.ts`; Model BPA keeps using the TS rule engine in `modelBpaApi.ts` — both already worked without the sidecar. The "compare with semantic-link-labs" inline panels on `MemoryPage.tsx` (HTML capture of `vertipaq_analyzer()`) and `ModelBpaPage.tsx` (raw `run_model_bpa` rows) were the last consumers and were removed. With no callers left, this bump deletes:

- `Developer Hub/SllSidecar/` (whole dir: `app.py`, `Dockerfile`, `requirements.txt`)
- `sll-sidecar` service + `SLL_SIDECAR_URL` env line in `docker-compose.yaml`
- SLL proxy block in `Backend/src/api/agenthub_controller.py` (`_SllRequest`, `_SllVertipaqRequest`, `_sll_base_url`, `_sll_post`, `POST /api/pbi-fixer/sll/model-bpa`, `POST /api/pbi-fixer/sll/vertipaq`)
- `Frontend/src/components/PbiFixer/services/sllApi.ts`
- "SLL sidecar" mention in `docs/index.html` Local Dev card

`docker compose --profile prod up` now starts only `backend` + `frontend` + `dev-gateway`; no more QEMU x86_64 emulation requirement on aarch64 dev hosts. Closes WS-U U1/U2/U3/U4 and supersedes WS-T T4/T5/T6 (which all depended on the sidecar staying alive).

### v0.81 — WS-U start: SLL Sempy Runner inline path removed (May 2026)
First slice of WS-U. The Sempy Runner page no longer offers "Run inline (SLL sidecar)"; every function goes through the notebook path (Fabric Spark already ships `sempy` + `sempy-labs` preinstalled). Removed from `SempyRunnerPage.tsx`: `sllApi` imports, all `sll*` state + `onRunSll` handler, the inline-run button, the dual helper-text variant, the SLL error MessageBar, the BPA results table, and the Vertipaq HTML pane (-126 / +6 lines).


Second parity batch on top of v0.59. **DAX formatting**: new `formatDax(dax)` in `fabricApi.ts` POSTs to `https://www.daxformatter.com/api/daxformatter/DaxFormat` (form-urlencoded). "Format DAX" button in the Expression header runs it on the selected measure's pending expression. CORS / network failure path falls back to copying the expression to the clipboard and opening daxformatter.com in a new tab so the user can paste manually. **Editable column / table / relationship properties**: new `ColumnEdit` / `TableEdit` / `RelationshipEdit` types + new patchers (`patchColumnInTmdl`, `patchTableInTmdl`, `patchRelationshipInTmdl`) factored on top of a generic `patchTmdlBlockProps` helper (header regex + indent unit detection + two-pass replace+insert; mirrors the measure version minus the expression-rewrite pass). Relationships are matched in TMDL by `fromColumn:`/`toColumn:` body lines (the GUID header isn't surfaced in `ModelData`); the patcher tries both `definition/relationships.tmdl` and `definition/model.tmdl` and mutates whichever part actually contains the matching block. New `pendingColumnEdits` / `pendingTableEdits` / `pendingRelEdits` state + `setColumnEdit` / `setTableEdit` / `setRelEdit` helpers + a unified `handleSaveEdits` that runs all four `update*Properties` calls in parallel and post-patches local `ModelData` so the UI reflects saved values immediately. Properties panel switched to `PropEditRow` for editable fields (column: summarizeBy / displayFolder / dataCategory / isHidden; table: description / isHidden; relationship: isActive / crossFilteringBehavior). Also: `parseTmdlDefinition` now captures table-level `description:` / `isHidden:` lines (previously dropped on the floor) so initial values populate correctly. Save button / pending counter now sums across all four object kinds (`totalPendingEdits`).

### v0.59 — Explorer parity batch 1: hierarchies, partitions, preview, perspective filter, context menu, JSON preview (May 5 2026)
First parity batch closing six Explorer / Tab gap items in one shipping bump.

**Model Explorer:**
- **Hierarchy levels rendering in tree** — `parseTmdlDefinition` now parses `level <name>` blocks (with `currentLevel` indent guard so nested `column:` props don't leak); tree emits `level:<t>:<h>:<i>` child rows under expanded hierarchies. New `hierarchy` + `level` cases in `propertiesContent`.
- **Partition details in properties** — table props panel expands every partition (name + sourceType + expression). Standalone `partition:<t>:<n>` selection also rendered.
- **Table data preview (TOPN DAX query + render)** — "Preview data (TOPN 100)" button on the table props panel calls existing `executeDax` with `EVALUATE TOPN(100, '<Table>')`; renders inline sticky-header HTML grid (max 280 px, scrolls).
- **Perspective filtering** — `Dropdown` in the toolbar, auto-loaded from existing `loadPerspectives`. `perspectiveFilteredOptions` post-filters tree by membership using parsed `Table` / `Table[Col]` / `Table::Hierarchy` paths.
- **Right-click context menu actions** — `onContextMenu` on tree rows opens a fixed-position floating menu (custom `CtxItem` component, NOT Fluent `Menu` — that needs an anchor for positioning) with Copy DAX reference / Copy node key / Preview data (tables) / Copy expression (measures).
- **Relationship read-only props** — captured as a freebie while wiring the tree.

**Report Explorer:**
- **Visual config JSON preview** — added `rawJson?: unknown` to `VisualInfo` + `PageInfo`, populated in `parseReportDefinition`, exposed via `getPageProperties` / `getVisualProperties`. New `JsonPreview` collapsible (▶/▼ + Copy button, monospace `<pre>` max-height 280 px) in both pageProps and visualProps blocks.

### v0.57 — WS-L "About" moved to AgentHub shell + WS-O-ICON workload glyph (May 2026)
Two cleanup workstreams closed in one bump.

**WS-L revised — About out of PBI Fixer.** About lives in the AgentHub shell footer above Support; the Fixer-level page is gone. Files: [`Frontend/src/components/AgentHub/AboutPage.tsx`](../../AgentHub/AboutPage.tsx) (new), [`Frontend/src/components/AgentHub/AgentHubLayout.tsx`](../../AgentHub/AgentHubLayout.tsx) `sidenav-footer-item` opens About via `openTab({ id: "about", kind: "about", … })`, `Frontend/src/components/PbiFixer/components/pages/AboutPage.tsx` deleted, `about` NavKey removed from `types/nav.tsx`. Follow-up (low-prio): confirm About content lists Lukasz + Alexander as authors, credits Michael Kovalsky for `semantic-link-labs`, and surfaces a single source-of-truth hub version (workload version constant lives in topbar — see WL-1 C8).

**WS-O-ICON — workload glyph.** Custom `developerHub.png` ships at [`Frontend/Package/assets/images/developerHub.png`](../../../../Package/assets/images/developerHub.png) and is wired into `Product.json` for both `favicon` and `icon.name` (top-level workload icon shown in the trust dialog + workload chooser + "New" tile). Stock `briefcase.png` removed from the assets folder. AgentHubItem `icon`/`activeIcon` stays on `execute.png` per the v0.56 lesson.

### v0.56 — Remove WS-K Script Runner + revert AgentHubItem icon (May 2026)
User dropped Script Runner from the roadmap ("not a fan"). Removed the `scriptRunner` NavKey + nav row + `Code20Regular` icon import in `types/nav.tsx`, the `ScriptRunnerPage` stub in `pages/stubs.tsx`, the import + switch case + `needsBothPickers` term in `PbiFixerPage.tsx`. PLAN.md WS-K section + Appendix A deleted; Script Runner now listed under Non-Goals so it does not get re-proposed. **Also reverted** the AgentHubItem icon swap (commit `7f1d8db`): `developerHub.png` is the large color tile and is not a valid item-type `icon`/`activeIcon` glyph — it broke the workload's exception handler ("Could not handle exception: undefined" dialog on AgentHub home). Restored `execute.png` for `AgentHubItem.icon`/`activeIcon`. **Lesson**: Fabric workload `Item.json` `icon`/`activeIcon` requires a small monochrome glyph (the play ▶ stays). The big color tile lives in `Product.json` `icon.name` for the workload chooser; the two are not interchangeable.

### v0.55 — WS-O Decision #6: URL-driven nav, drop sessionStorage (May 2026)
Closing the last open WS-O item. The PBI Fixer sub-page selection (Model / Report / 13 stubs) is now driven exclusively by the URL `?nav=` query — the legacy `sessionStorage["pbiFixer.activeNav"]` write/read is gone in both `PbiFixerPage.tsx` and `AgentHubLayout.tsx`. A `popstate` listener in `PbiFixerPage` syncs `activeNav` to the URL on browser back/forward so history navigation works as expected. Deep-links like `?nav=delta` continue to land directly on the target page on first load. The `pbiFixer.expandedGroups` sessionStorage entry is intentionally kept (per Decision #6 it is not part of activeNav state). All other WS-O decisions (#1 connection bar warm bg, #2 shared sub-nav classes via `pbifixer-subnav-item` in `AgentHubLayout`, #3 no topbar right cluster, #7 no sidebar footer, #9 120 ms crossfade, #10 `ready: false` filter) were already shipped across earlier versions.

### v0.54 — User-facing 'dataset' → 'semantic model' (May 2026)
All user-visible strings now use the official term "semantic model" instead of the legacy "dataset". Changes: (a) `ModelExplorer.tsx` error "Workspace and dataset name required" → "Workspace and semantic model required". (b) `sempyCatalog.ts` description "reports, datasets, lakehouses" → "reports, semantic models, lakehouses". (c) `SempyRunnerPage.tsx` field label maps `kind: "dataset"` to display text `(semantic model)`. Internal Python kwarg names (`dataset=...` in generated sempy code) and `kind: "dataset"` discriminator stay — they are the literal sempy.fabric API surface and cannot change.

### v0.53 — Hide auto-bound Sempy params (May 2026)
Sempy Runner param grid now hides any param whose `kind` is `workspace` / `dataset` / `report` when the connection bar already provides a value. They were just duplicating the picker above. Empty-state copy is now "All parameters are auto-bound from the connection bar above." when every param resolves to a connection-bar value. Pure UX change in `SempyRunnerPage.tsx` — generated Python is unchanged (auto-bind still flows through `valueFor`).

### v0.52 — Merged Semantic Model / Report picker (May 2026)
On pages that target either scope (Fixer, Sempy Runner, Script Runner) the connection bar now renders a **single** `Semantic Model / Report` picker instead of two separate dropdowns. Items are deduped by `<folderId>|<name>`, so a model and report sharing the same name in the same folder collapse into one option; selecting it sets `datasetId` AND `reportId` simultaneously. Items present on only one side are tagged `· model only` / `· report only` so the user can still target a single scope when needed. Pure UX change in `PbiFixerPage.tsx` — no backend / fixer registry changes.

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
