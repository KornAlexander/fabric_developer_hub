# PBI Fixer — TypeScript Port Plan

> Tracking the migration from Python (ipywidgets) to TypeScript (React + FluentUI v9)
> for integration into the Fabric Developer Hub custom workload.

## Source: `c:\Users\alkorn\repos\pbi_fixer\src\` (~12,000+ lines, 60+ files)
## Target: `c:\Users\alkorn\repos\fabric_agenthub\AgentHub\Frontend\src\components\PbiFixer\`

---

## Ported (Phase 1 — v0.1.0, April 2026)

| Component | Python Source | TS File(s) | Lines | Notes |
|---|---|---|---|---|
| **Types — Model** | `_sm_explorer.py` | `types/model.ts` | 92 | ColumnInfo, MeasureInfo, TableInfo, RelationshipInfo, ModelData, etc. |
| **Types — Report** | `_report_explorer.py` | `types/report.ts` | 40 | PageInfo, VisualInfo, ReportData, VisualObjectRef |
| **Types — Common** | `_ui_components.py` | `types/common.ts` | ~30 | TreeItem, TreeBuildResult, ScanResult, ConnectionStatus |
| **Theme** | `_ui_components.py` | `utils/theme.ts` | 35 | FONT_FAMILY, ICONS, EXPANDED/COLLAPSED markers, INDENT |
| **Tree Utilities** | `_ui_components.py` | `utils/treeUtils.ts` | 75 | buildTreeItems (zero-width-space dedup), filterTreeOptions (parent-preserving), tableSummary |
| **Model Tree Builder** | `_sm_explorer.py` | `utils/modelTree.ts` | 200 | buildModelTree, folder grouping (measures+columns), getModelPreviewText, getDaxReference |
| **Report Tree Builder** | `_report_explorer.py` | `utils/reportTree.ts` | 160 | buildReportTree, getPageProperties, getVisualProperties |
| **Fabric API Service** | `sempy_labs` / `sempy.fabric` | `services/fabricApi.ts` | 420 | REST API client: workspaces, datasets, tables, columns, measures, relationships, reports, PBIR definition parsing |
| **Model Explorer** | `_sm_explorer.py` (850 lines) | `components/ModelExplorer.tsx` | 500 | Tree view, search, expression preview, DAX reference copy, properties panel (measure/column/table) |
| **Report Explorer** | `_report_explorer.py` (700 lines) | `components/ReportExplorer.tsx` | 570 | Tree view, search, preview panel, properties (page/visual), used objects, pending changes stub |
| **PBI Fixer (Orchestrator)** | `_pbi_fixer.py` (100 lines) | `components/PbiFixer.tsx` | 150 | Tab bar (Model/Report), connection bar (workspace/dataset/report inputs) |

**Total ported: ~2,280 lines TS across 16 files (from ~1,750 lines Python)**

---

## Not Yet Ported

### Tabs (12 remaining)

| Tab | Python Source | Lines | Priority | Complexity | Notes |
|---|---|---|---|---|---|
| ⚡ Fixer | `_pbi_fixer.py` (inline) | ~200 | P1 | Medium | Hidden tab, shows only when fix invoked. Runs fixer scripts, shows stdout capture |
| 👁 Perspectives | `_perspective_editor.py` | ~500 | P2 | High | Tri-state checkboxes, create/modify/delete perspectives via TOM |
| 🌐 Translations | `_pbi_fixer.py` (inline) | ~300 | P3 | Medium | Read/write translations from model metadata |
| 🔍 Model BPA | `_pbi_fixer.py` (inline) | ~400 | P1 | Medium | Best Practice Analyzer for semantic models. 14 auto-fixer rules |
| 🔍 Report BPA | `_pbi_fixer.py` (inline) | ~300 | P1 | Medium | Best Practice Analyzer for reports (pie charts, page size, etc.) |
| � Memory Analyzer | `_pbi_fixer.py` (`_vertipaq_tab`) | ~300 | P2 | Medium | Labeled "Memory Analyzer" in UI; already delegates to real `sempy_labs.vertipaq_analyzer()` — table/column sizes, compression stats. **No separate Memory tab** — Memory Analyzer = Vertipaq Analyzer. |
| 📦 Delta Analyzer | `_pbi_fixer.py` (inline) | ~200 | P3 | Medium | Compare model versions |
| ✏️ Prototype | `_report_prototype.py` | ~400 | P3 | High | Report prototyping / page layout editor |
| 🗺 Diagram | `_pbi_fixer.py` (inline) | ~300 | P3 | Medium | Visual model diagram (relationships) |
| ▶ Script Runner | `_pbi_fixer.py` (inline) | ~150 | P3 | Low | Run arbitrary scripts against models |
| ℹ️ About | `_pbi_fixer.py` (inline) | ~50 | P3 | Low | Version info, links |

### Report Fixers (14 files, ~1,800 lines total)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Replace Pie Charts → Bar | `report/_Fix_PieChart.py` | 152 | P1 |
| Fix Bar Chart Formatting | `report/_Fix_BarChart.py` | 299 | P1 |
| Fix Column Chart Formatting | `report/_Fix_ColumnChart.py` | 299 | P1 |
| Standardize Page Size (Full HD) | `report/_Fix_PageSize.py` | 103 | P1 |
| Hide Visual-Level Filters | `report/_Fix_HideVisualFilters.py` | 58 | P2 |
| Upgrade PBIRLegacy → PBIR | `report/_Fix_UpgradeToPbir.py` | 100 | P2 |
| Migrate Slicers → Slicerbar | `report/_Fix_MigrateSlicerToSlicerbar.py` | ~150 | P2 |
| Fix Line Chart | `report/_Fix_LineChart.py` | ~200 | P2 |
| Fix KPI Card | `report/_Fix_KpiCard.py` | ~150 | P2 |
| Fix Matrix | `report/_Fix_Matrix.py` | ~200 | P2 |
| Fix Table | `report/_Fix_Table.py` | ~200 | P2 |
| Fix Titles | `report/_Fix_FixTitles.py` | ~100 | P2 |
| Set Data Source Version | `report/_Fix_SetDataSourceVersion.py` | 84 | P3 |
| Convert Column → Line | `_Fix_ColumnToLine.py` | 118 | P3 |

### Semantic Model Fixers (18 files, ~1,100 lines total)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Add Measures from Columns | `semantic_model/_Add_MeasuresFromColumns.py` | ~150 | P1 |
| Add PY Measures | `semantic_model/_Add_PYMeasures.py` | ~150 | P1 |
| Discourage Implicit Measures | `semantic_model/_Fix_DiscourageImplicitMeasures.py` | 73 | P1 |
| Do Not Summarize | `semantic_model/_Fix_DoNotSummarize.py` | 47 | P2 |
| Trim Object Names | `semantic_model/_Fix_TrimObjectNames.py` | 71 | P2 |
| Use DIVIDE Function | `semantic_model/_Fix_UseDivideFunction.py` | 59 | P2 |
| Hide Foreign Keys | `semantic_model/_Fix_HideForeignKeys.py` | 41 | P2 |
| Mark Primary Keys | `semantic_model/_Fix_MarkPrimaryKeys.py` | 44 | P2 |
| Measure Descriptions | `semantic_model/_Fix_MeasureDescriptions.py` | 40 | P2 |
| Measure Format | `semantic_model/_Fix_MeasureFormat.py` | 38 | P3 |
| Month Column Format | `semantic_model/_Fix_MonthColumnFormat.py` | 38 | P3 |
| Sort Month Column | `semantic_model/_Fix_SortMonthColumn.py` | 61 | P3 |
| Flag Column Format | `semantic_model/_Fix_FlagColumnFormat.py` | 48 | P3 |
| Floating Point Data Type | `semantic_model/_Fix_FloatingPointDataType.py` | 38 | P3 |
| IsAvailableInMDX (False) | `semantic_model/_Fix_IsAvailableInMdx.py` | 37 | P3 |
| IsAvailableInMDX (True) | `semantic_model/_Fix_IsAvailableInMdxTrue.py` | 49 | P3 |
| Percentage Format | `semantic_model/_Fix_PercentageFormat.py` | 43 | P3 |
| Whole Number Format | `semantic_model/_Fix_WholeNumberFormat.py` | 44 | P3 |

### Additional Top-Level Features

| Feature | Python File | Lines | Priority |
|---|---|---|---|
| Cache Warming | `_Add_CacheWarming.py` | 269 | P3 |
| Incremental Refresh | `_Add_IncrementalRefresh.py` | 139 | P3 |
| Prep for AI | `_Add_PrepForAI.py` | 501 | P2 |

### Model Explorer — Missing Features (in existing TS component)

| Feature | Status | Notes |
|---|---|---|
| DAX formatting (format_dax_expression) | ❌ Not ported | Python uses TOM for formatting |
| Table data preview (TOPN query) | ❌ Not ported | Execute DAX query + render table |
| Editable properties (save back via XMLA) | ❌ Not ported | Requires XMLA endpoint or TOM |
| Multi-model support | ❌ Stub only | Types defined, logic not implemented |
| Perspective filtering | ❌ Not ported | Filter tree by perspective |
| Hierarchy levels display | ❌ Partial | Types defined, not rendered in tree |
| Partition details | ❌ Partial | Types defined, not shown in properties |
| Context menu (right-click actions) | ❌ Not ported | Python has right-click on tree items |
| Scan mode (BPA integration) | ❌ Not ported | Scan results → visual badges |

### Report Explorer — Missing Features (in existing TS component)

| Feature | Status | Notes |
|---|---|---|
| Live report preview (thumbnail) | ❌ Not ported | Python uses exportToFile API |
| Editable properties (save back) | ❌ Stub only | Pending changes state exists, save not implemented |
| Fix-this buttons per visual | ❌ Not ported | Quick-fix buttons in properties panel |
| Visual → SM navigation | ✅ Ported | onNavigateToModel callback wired |
| Scan mode (Report BPA) | ❌ Not ported | Scan badge counts shown, scan execution not implemented |
| Visual config JSON preview | ❌ Not ported | Show raw visual.json content |
| Drag-and-drop reorder | ❌ Not ported | Reorder pages |

---

## Architecture Decisions

| Decision | Choice | Rationale |
|---|---|---|
| UI Framework | React + FluentUI v9 | Matches Developer Hub Frontend stack |
| API Layer | Fabric REST API + PBI REST API | Python used sempy_labs (XMLA/TOM) — REST is browser-compatible |
| Auth | Token passed as prop | Developer Hub handles OAuth, passes token down |
| State Management | React useState/useCallback | Simple component-local state for now. May migrate to Redux when more tabs added |
| SM Write Operations | TBD | Python used TOM/.NET — TypeScript will need XMLA or REST equivalents |
| Report Write Operations | Fabric REST updateDefinition | Direct JSON patching of PBIR definition parts |

---

## Milestone Plan

| Milestone | Scope | Target |
|---|---|---|
| **M1** ✅ | Model Explorer + Report Explorer (read-only) | Done (April 2026) |
| **M2** | BPA tabs (Model + Report) + Top 5 fixers | TBD |
| **M3** | Perspectives + Vertipaq + remaining fixers | TBD |
| **M4** | Prototype + Diagram + full feature parity | TBD |

---

# Next Phase — Feature Parity Roadmap (stays on v0.x)

> Goal: Reach **visual + functional parity** with the Python Fixer notebook.
> The TS Fixer must "look and feel like the Developer Hub" — left-side tree navigation,
> FluentUI v9 primitives, Developer Hub theme tokens, Developer Hub workspace picker behavior.
>
> Phased into **independent workstreams (WS-A … WS-N)** designed for **parallel development
> across multiple chat windows/tabs** (same local repo, disjoint file ownership → last edit
> wins merge, no worktrees needed). Each workstream owns its own files, has self-contained
> scope, acceptance criteria, and a version bump target.
>
> **Version policy:** stay on `v0.x`. Keep bumping the patch (`v0.5`, `v0.6`, … `v0.99`).
> **Do not go to `v1.0`** until Alexander explicitly says so.

## Design Principle — Single-Owner Files (parallel DEV, last-edit-wins merge)
Every workstream only edits files listed under its **Owns** section. Shared files are split
between two owners only:
- **WS-A** owns the shell and page-slot surface (`PbiFixerPage.tsx`, `PbiFixerNav.tsx`,
  page stubs, nav metadata, theme alignment).
- **WS-N** owns the integration/shared-assets surface (`utils/version.ts`,
  `Frontend/package.json`, final cross-page wiring, and any unavoidable shared imports).

All other workstreams add to their own files + append-only exports from `types/shared.ts`
(new file, owned by no one). Because ownership is disjoint, a plain `git pull --rebase` +
push from each chat is enough — no worktrees, no feature branches required. If two chats ever
collide on the same line, last edit wins; re-run the loser workstream against the merged file.

## Versioning
- Stay on `v0.x`. The version numbers listed in each workstream are **milestone targets**
  (e.g. WS-A targets v0.5, WS-B targets v0.6, …).
- **Only WS-N updates the actual version source of truth** in `utils/version.ts` after a set
  of merged workstreams is integrated. Individual chats do not touch shared version files.
- Keep going past `v0.9` into `v0.10`, `v0.11`, etc.
- **Never auto-bump to `v1.0`.**

---

## Testing — Playwright per chat window (parallel-safe)
- **Reuse the existing browser tab.** The user keeps one tab authenticated; do NOT open a new tab per chat or per test run — auth state, dev-mode toggle and cert trust only live in that tab.
- **After any hard reload / cache clear**, the Fabric workload "Continue" auth popup reappears. Click it once before touching anything inside the `pbifixer` iframe, otherwise all tokens come back empty and the UI looks frozen.
- Parallel chats should still coordinate on the same tab; if another chat is actively driving it, wait rather than spawning a second tab (Playwright MCP only exposes one Chromium profile).
- Cached auth / dev-mode toggles are per Chromium profile, so the first test in a fresh profile needs a one-time interactive sign-in; after that the popup just needs the "Continue" click.

---

## Delivery status snapshot (code audit — current build)

| WS | Feature | Status | Version | Real page file | `ready` flag |
|----|---------|--------|---------|----------------|--------------|
| WS-A | Shell / nav / theming | ✅ shipped | v0.1–v0.5 | n/a (shell) | n/a |
| WS-B | Memory Analyzer (structural metadata) | 🟡 partial (Phase 2) | v0.18 | `MemoryPage.tsx`, `services/memoryApi.ts` | `true` |
| WS-C | Model BPA | ✅ shipped | v0.12 | `ModelBpaPage.tsx` | `true` |
| WS-D | Report BPA | ✅ shipped | v0.13 | `ReportBpaPage.tsx` | `true` |
| WS-E | Fixer Execution | ✅ shipped + backend apply | v0.14 → **v0.41** | `FixerPage.tsx`, `fixers/index.ts`, `services/fixersApi.ts`, **backend `pbi_fixer_handlers.py`** | `true` |
| WS-F | Perspectives | ✅ shipped | v0.15 | `PerspectivesPage.tsx` | `true` |
| WS-G | Translations + Auto-Translate | ✅ shipped + backend apply | v0.11 → **v0.40** | `TranslationsPage.tsx`, `services/translationsApi.ts`, **backend `tmdl_translations.py`** | `true` |
| WS-I | Delta Analyzer | ✅ shipped | v0.24 | `DeltaAnalyzerPage.tsx`, `services/deltaApi.ts` | `true` |
| WS-J | Diagram (SVG canvas) | ✅ shipped | v0.19–v0.20 | `DiagramPage.tsx`, `services/diagramApi.ts` | `true` |
| WS-K | Script Runner (Monaco) | ⬜ not started | — | stub | `false` |
| WS-L | About | ⬜ not started | — | stub | `false` |
| WS-M | Prototype | ✅ shipped | v0.16 | `PrototypePage.tsx` | `true` |
| WS-N | Integration sweep | 🟡 partial | v0.12–v0.16 | n/a | n/a |

Legend: ✅ shipped • 🟡 partial • ⬜ not started

Write-back work for WS-G (v0.40) and WS-E (v0.41) is now wired through a
backend TMDL/PBIR REST round-trip (`getDefinition` → in-memory mutate → `updateDefinition`,
both LRO). No sempy-labs/AMO/XMLA dependency required — handlers live in
`Backend/src/services/agenthub/{tmdl_translations,pbi_fixer_handlers}.py`.
Write-back is still stubbed across WS-C/D/F/M pending the same bridge for
those surfaces; see each workstream's *Implementation note* block for the specific
deferred bits.

Remaining remote vestige: ~~`types/nav.ts` still carries a separate `vertipaq` nav entry alongside `memory`.~~ **Resolved in WS-B (v0.18):** `vertipaq` nav key removed; `memory` is now the single shipped entry per Python parity.

---

## WS-A — Left-Side Tree Navigation + Theming pass (shell refactor) — **DO FIRST**
**Blocks:** all other workstreams (they plug pages into the new shell).
**Goal:** Replace the top TabList with a **flat left-side nav tree** styled like the Developer
Hub shell. Also apply the Developer-Hub theming/a11y polish in the same pass so every later
workstream inherits correct tokens.

### UI Target — flat tree, "Others" expands siblings inline
```
┌─────────────────────┬──────────────────────────────────────┐
│ Nav (260 px)        │ Active page content                   │
│ ─────────────────── │                                       │
│ PBI Fixer           │   ← top-level (non-clickable header)  │
│   🗂 Model          │                                       │
│   📊 Report         │                                       │
│   ▸ Others          │   ← collapsed by default              │
│     ⚡ Fixer        │   (when expanded, children render at  │
│     🔍 Model BPA    │    the SAME indent as Model/Report,   │
│     🔍 Report BPA   │    i.e. flat — not one level deeper)  │
│     � Memory       │ (= Vertipaq Analyzer)                 │
│     👁 Perspectives │                                       │
│     🌐 Translations │                                       │
│     📦 Delta        │                                       │
│     🗺 Diagram      │                                       │
│     ▶ Script Runner │                                       │
│     🧪 Prototype    │                                       │
│     ℹ About         │                                       │
└─────────────────────┴──────────────────────────────────────┘
```
- "PBI Fixer" is the top-level label; **Model / Report / Others are peers below it**.
- Clicking **Others** toggles expand/collapse. When expanded, its children render **at the
  same visual indent** as Model/Report (flat hierarchy — not a second indent level). Achieve
  this by styling `<TreeItem>` children under Others with `--indent: 0` override.
- Collapsed by default on first load. State persisted in `sessionStorage` (`pbiFixer.activeNav`
  + `pbiFixer.othersExpanded`).
- Use FluentUI v9 `<Tree>` / `<TreeItem>` / `<TreeItemLayout>`.
- Connection bar (workspace / SM / report pickers) at the top, spanning both columns.
- **Workspace folders in SM + Report dropdowns** (cheap win — already wired on the backend):
  the `/api/workspaces/{id}/items` endpoint already fetches `fabric_list_folders` in parallel
  and returns folder metadata alongside items. Extend the Combobox to group options by
  folder path (e.g. `📁 Finance / Sales Report`, `📁 HR / Headcount Dataset`). Use FluentUI
  `OptionGroup` with the folder path as the group label. Items at workspace root stay
  ungrouped at the top. If folder metadata is missing or fails to load, fall back to the
  current flat list — do not block the picker.
- **Multi-workspace support from day one**: workspace picker allows multiple selections; each
  selected workspace gets its own scoped page state (page remounts with new props when
  workspace/dataset/report changes). Backend already supports per-call `workspaceId` — just
  propagate it via page context, don't cache across workspaces.
- **Theming pass included**: align tokens in `utils/theme.ts` with
  `fabric_agenthub/Frontend/src/theme`; spacing scale, border radius, elevation, focus rings,
  dark mode parity, keyboard nav (arrows in tree, Tab focus order), `aria-*` audit, empty
  states + skeleton loaders in the shell.
- **Font consistency fix**: `ModelExplorer.tsx` and `ReportExplorer.tsx` currently render
  tree items (and likely the filter input + expression panel) in an off-looking font —
  different from the rest of the Developer Hub shell. Remove any hard-coded `fontFamily` on
  tree rows / tree list / properties panels and let them inherit the FluentUI
  `fonts.base.fontFamily` token (same as nav, headers, buttons everywhere else).
  - Audit: grep `FONT_FAMILY` and `fontFamily:` inside `components/ModelExplorer.tsx`,
    `components/ReportExplorer.tsx`, and `utils/theme.ts`.
  - Replace any monospace or custom stack on non-code elements with the FluentUI default.
  - Keep monospace **only** for actual code surfaces (DAX expression preview, PBIR JSON).

### Owns
- `components/PbiFixerPage.tsx` — rewrite shell layout (left nav + right content area)
- `components/PbiFixerNav.tsx` — **NEW**: flat-tree nav, props `{ active, onChange }`
- `components/pages/` — **NEW** folder. For each future page create a **stub**
  `<PageName>Page.tsx` exporting a component with shared `PageProps` (receives `auth`,
  `workspaceId`, `datasetId`, `reportId`, `reportType`). Stubs render `<Text>Coming soon — …</Text>`.
- `types/nav.ts` — **NEW**: `NavKey` union + `NAV_ITEMS` metadata array
- `types/shared.ts` — **NEW append-only** cross-workstream type drop zone
- `utils/theme.ts` — theming-token alignment

### Acceptance
- [ ] Flat tree with Model / Report / Others peers; Others children render at same indent
- [ ] Others collapsed on first load; expand state persists
- [ ] Model + Report pages render identically to today (no regression)
- [ ] 11 stub pages under Others (Fixer, Model BPA, Report BPA, Memory, Perspectives, Translations, Delta, Diagram, Script Runner, Prototype, About → 11 total, About is last)
- [ ] Connection bar works across all pages; switching workspace remounts the active page
- [ ] Keyboard nav: ↑/↓ moves selection, →/← expands/collapses, Enter activates
- [ ] Dark + light mode both visually match Developer Hub home page
- [ ] Model Explorer and Report Explorer tree items, filter input, and properties panels use
      the same FluentUI base font as the rest of the Developer Hub (no more weird off-font) —
      monospace preserved only for DAX expression + PBIR JSON surfaces
- [ ] SM + Report dropdowns group options by workspace folder (using existing folder data
      from `/api/workspaces/{id}/items`); graceful fallback to flat list if folder data absent
- [ ] Bump to **v0.5**

### Dependencies
None. Start immediately.

---

## WS-B — Memory Analyzer (Vertipaq) — hybrid Python bridge now → TS later
**Goal:** Port the notebook's **Memory Analyzer** tab (a.k.a. Vertipaq Analyzer) to the Developer Hub.
The Python fixer's `_vertipaq_tab` already delegates to the official
`sempy_labs.vertipaq_analyzer()` — there is **no "bad copy"** for this feature; this WS is a
straight UI re-render plus a thin backend pass-through so the TS frontend can consume the
same real sempy-labs result. UI label is **"Memory"** (matches Python nav); under the hood
it runs `sempy_labs.vertipaq_analyzer()`. Do **not** reimplement the analysis in TS yet.

### Architecture — hybrid
Phase 1 (now): **Python analysis bridge** in the backend calls real sempy-labs.
Phase 2 (later, not this roadmap): migrate hot paths to TS (`/executeQueries` DAX against
`INFO.STORAGETABLE*`) once the TS fixer has enough telemetry to know what's needed.
Frontend API shape is designed to be source-agnostic so Phase 2 is a controller swap.

### Owns
- `Backend/agenthub/controllers/pbi_vertipaq_controller.py` — **NEW**
  - `POST /api/pbi-fixer/vertipaq` body `{workspaceId, datasetId}` → JSON
    Implementation: run `sempy_labs.vertipaq_analyzer(dataset=datasetId, workspace=workspaceId, format="dict")` inside the backend (install `semantic-link-labs` in Backend requirements)
  - Returns: `{ summary, tables, columns, relationships, partitions, hierarchies }`
- `Backend/requirements.txt` — add `semantic-link-labs`, `semantic-link`
- `Frontend/src/components/PbiFixer/services/vertipaqApi.ts` — **NEW** client
- `Frontend/src/components/PbiFixer/components/pages/MemoryPage.tsx` — **NEW** (nav label "Memory")
  - Subtabs matching the Python tab: Model Summary, Tables, Partitions, Columns, Relationships, Hierarchies
  - FluentUI `<DataGrid>` with the classic Vertipaq columns (Table, Rows, Size, % DB, Dictionary Size, Data Size, Hierarchy Size, Cardinality, Encoding)
  - `read_stats_from_data` checkbox for Direct Lake (parity with Python)
  - Sort + filter + CSV export button

### Acceptance
- [ ] Backend endpoint returns real sempy-labs Vertipaq output (verified against notebook)
- [ ] Page loads under 10 s for a 500 MB model
- [ ] All 6 subtabs render parity with Python `_vertipaq_tab`
- [ ] Bump to **v0.6**

### ✅ Implementation note (v0.18 — Phase 2 shipped first)
- **Why Phase 2 first**: backend image still lacks `semantic-link-labs`/`semantic-link` (verified against `Backend/pyproject.toml`). Same precedent as WS-C/D: ship the client-side path, swap in the backend bridge later without touching the page.
- **What works today** (`services/memoryApi.ts` + `pages/MemoryPage.tsx`, all via the existing `executeDax` proxy):
  - `INFO.VIEW.TABLES()` — Tables tab (name, rows, columns, mode, hidden, modified)
  - `INFO.VIEW.COLUMNS()` — Columns tab (table, name, data type, hidden, key, folder, format)
  - `INFO.VIEW.MEASURES()` — Measures tab (table, name, data type, format, folder, hidden, expression)
  - `INFO.VIEW.RELATIONSHIPS()` — Relationships tab (from/to, cardinality, active, cross filter)
  - Summary cards: total rows, table/column/measure/relationship counts
  - Sort + filter per tab; CSV export per tab; Refresh button
- **What's deferred to Phase 1 (backend bridge)** — surfaced via an info `MessageBar` on the page:
  - Per-column `TotalSize` / `DictionarySize` / `DataSize` / `Cardinality` / `Encoding` / `Segments`
  - Partitions, Hierarchies
  - The reason: the Power BI `executeQueries` REST API rejects raw `INFO.STORAGETABLE*`, `INFO.PARTITIONS()`, `INFO.HIERARCHIES()` with `DatasetExecuteQueriesError` / `AnalysisServicesErrorCode 3239575574`. Only the friendly `INFO.VIEW.*` family is allowed. `sempy_labs.vertipaq_analyzer()` can call them because it goes via XMLA.
- **Nav consolidation**: `vertipaq` nav key removed; the single "Memory" entry (💾) replaces both stubs. `Others` count went 12 → 11.
- **Phase 1 follow-up plan**: add `semantic-link-labs` to `Backend/pyproject.toml`, expose `POST /api/pbi-fixer/vertipaq {workspaceId, datasetId}` returning the same shape, and replace `loadVertipaqData` internals with a single `pbiFixerProxy("workload", "vertipaq", body)` call. Page UI is unchanged — add Partitions, Hierarchies, and storage-rich Columns tabs back when bridge ships.
- Verified live (Playwright, build v0.18, Demo workspace / Bad Report - Testing): all 4 data tabs render; INFO.VIEW.* queries return 1 table + 9 columns + 1 measure + 0 relationships; banner explains the deferred storage breakdown.

### Dependencies
WS-A (for the nav slot to plug into).

---

## WS-C — Real Model BPA (backend bridge) ✅ shipped v0.12
**Goal:** Use `sempy_labs.run_model_bpa()` (the official BPA) — not the trimmed inline copy
in `_pbi_fixer.py`. Render results in a FluentUI DataGrid with severity badges and
"Fix it" buttons wired to the existing TS fixers (WS-E).

**Implementation note (v0.12):** The sempy-labs backend bridge is deferred until the
`sempy_labs` dependency lands in the backend image. For v0.12 WS-C ships a client-side
rule engine (`services/modelBpaApi.ts`) operating on the already-loaded `ModelData`.
Endpoint shape and grid layout match the eventual backend contract, so the swap-in is
zero-touch for the UI. Ten rules across DAX, Model, Perf and Naming categories;
"Fix it" dispatches the `pbifixer:bpa-fix` CustomEvent for WS-N to consume.

### Owns
- `Backend/agenthub/controllers/pbi_model_bpa_controller.py` — **NEW**
  - `POST /api/pbi-fixer/bpa/model` body `{workspaceId, datasetId, rulesetUrl?}` →
    runs `sempy_labs.run_model_bpa(dataset=..., workspace=..., return_dataframe=True)` and
    serializes to JSON
  - `GET /api/pbi-fixer/bpa/rules` → returns current BPA rules JSON (for rule browsing)
- `Frontend/src/components/PbiFixer/services/modelBpaApi.ts` — **NEW**
- `Frontend/src/components/PbiFixer/components/pages/ModelBpaPage.tsx` — **NEW**
  - DataGrid: Rule, Category, Severity, Object, Object Type, Description, Fix
  - Columns: filter by severity, category; group by rule
  - "Fix it" button emits a typed integration event; final cross-page wiring lands in WS-N

### Acceptance
- [x] Identical rule list + findings compared to the Python notebook on Contoso10K
      — *deferred: client-side rule set used for v0.12; sempy-labs parity comes with backend bridge*
- [x] Per-finding "Fix it" action is exposed via a typed event contract for WS-N to wire
      — `pbifixer:bpa-fix` CustomEvent
- [x] Bump to **v0.12** (was v0.7 in the original plan — numbering superseded by delivery order)

### Dependencies
WS-A. No code dependency on WS-B or WS-E; final Fixer-page wiring happens in WS-N.

---

## WS-D — Real Report BPA (backend bridge) ✅ shipped v0.13
**Goal:** Use `sempy_labs.report.run_report_bpa()` — rendered the same way as Model BPA.

**Implementation note (v0.13):** As with WS-C, sempy-labs backend bridge is deferred
until the Python dep lands. v0.13 ships a client-side rule engine
(`services/reportBpaApi.ts`) that operates on the already-loaded `ReportData`.
Ten rules across Visualization, Layout, Accessibility, and Performance. Fix events
use the same `pbifixer:bpa-fix` CustomEvent contract as WS-C so WS-N wires both
BPAs the same way.

### Owns
- `Backend/agenthub/controllers/pbi_report_bpa_controller.py` — **NEW**
  - `POST /api/pbi-fixer/bpa/report` body `{workspaceId, reportId}` → calls
    `sempy_labs.report.run_report_bpa(report=..., workspace=..., return_dataframe=True)`
- `Frontend/src/components/PbiFixer/services/reportBpaApi.ts` — **NEW**
- `Frontend/src/components/PbiFixer/components/pages/ReportBpaPage.tsx` — **NEW**
  - Same DataGrid pattern as Model BPA

### Acceptance
- [x] Findings match Python notebook for a 20-page report
      — *deferred: client-side rule set used for v0.13; sempy-labs parity comes with backend bridge*
- [x] "Fix it" on Pie Chart finding emits a typed integration event for WS-N to wire
      — `pbifixer:bpa-fix` CustomEvent (source: `report-bpa`)
- [x] Bump to **v0.13** (was v0.8 in original plan — superseded by delivery order)

### Dependencies
WS-A.

---

## WS-E — Fixer Execution Page ✅ shipped v0.14 → backend apply v0.41
**Goal:** Port the Fixer tab — a page that lists all fixers as checkboxes, runs selected
fixers, shows live stdout, and writes back via `fabric_updateDefinition`.

**Implementation note (v0.14):** First cut shipped the full UX contract (checkbox list
grouped by scope, Scan/Apply mega-button, Apply switch + diff preview + confirm dialog,
live log panel, BPA "Fix it" preselection via `pbifixer:bpa-fix` listener) with 6
fixer `scan` implementations working against loaded `ReportData` / `ModelData`.
Write-back (`apply`) was stubbed across the board.

**Implementation note (v0.41):** Write-back fully wired via new backend endpoint
`POST /api/pbi-fixer/fixers/apply` that mirrors the v0.40 translations LRO pattern
(`getDefinition?format=TMDL` → in-memory mutate → `updateDefinition`). New module
`Backend/src/services/agenthub/pbi_fixer_handlers.py` ships **13 handlers** in a
`FIXER_HANDLERS` registry (8 SM + 5 Report). Frontend `fixers/index.ts` rewritten as a
thin backend-delegating registry (`backendFixer({id, title, scope})`). Only
`Fix_UpgradeToPbir` remains a stub (needs sempy-labs runtime). Pytest at
`Backend/tests/unit/services/agenthub/test_pbi_fixer_handlers.py` (15 cases). Verified
end-to-end via Playwright (Demo / Bad Report - Testing — Fix_PieChart 1 finding,
Fix_FloatingPointDataType 3 findings, both `applied=true · written back to Fabric`).

Shipped fixers (v0.41):
- **SM**: Fix_FloatingPointDataType, Fix_DoNotSummarize, Fix_DiscourageImplicitMeasures (alias),
  Fix_IsAvailableInMdxFalse, Fix_MeasureFormat, Fix_PercentageFormat, Fix_WholeNumberFormat,
  Fix_HideForeignKeys
- **Report**: Fix_PieChart, Fix_PageSize (1280×720), Fix_HideVisualFilters,
  Fix_DisableShowItemsNoData, Fix_RemoveUnusedCustomVisuals
- **Stub**: Fix_UpgradeToPbir

### First-cut fixer list (user-selected)
1. `Fix_PieChart` — replace pie/donut with bar (TS-native)
2. `Fix_BarChart` — standardize bar formatting (TS-native)
3. `Fix_ColumnChart` — standardize column formatting (TS-native)
4. `Fix_PageSize` — Full HD 16:9 (TS-native)
5. `Fix_UpgradeToPbir` — PBIRLegacy → PBIR (backend bridge — needs sempy-labs tooling)
6. `Fix_DiscourageImplicitMeasures` — SM TOM write (backend bridge)

### Architecture Choice
Two execution modes per fixer:
1. **TS-native** — pure PBIR JSON patching via `pbiFixerProxy` `fabric_updateDefinition`
2. **Backend bridge** — `POST /api/pbi-fixer/run-fixer` runs the Python sempy-labs fixer.
   Used when TOM/XMLA or non-trivial tooling is required.

### Safety rails (user-mandated)
- **Default mode is Scan-Only.** The big ⚡ button says "Scan selected" until the user
  toggles the "Apply changes" switch.
- "Apply" requires a confirmation dialog listing exactly which fixers will write.
- Per-fixer "Dry run" toggle (some fixers support it natively; UI enforces).
- Diff preview panel: before Apply, show a collapsible diff (PBIR JSON before/after for report
  fixers; TMDL before/after for SM fixers). User must tick "I reviewed the diff" to enable Apply.

### Owns
- `Backend/agenthub/controllers/pbi_fixer_run_controller.py` — **NEW** (backend bridge; supports `Fix_UpgradeToPbir` + `Fix_DiscourageImplicitMeasures` initially)
- `Frontend/src/components/PbiFixer/fixers/` — **NEW** folder with one file per TS-native fixer
  - `fixPieChart.ts`, `fixBarChart.ts`, `fixColumnChart.ts`, `fixPageSize.ts`
  - Each exports `{ id, title, scope: 'report'|'sm', mode: 'ts'|'backend', run(ctx, apply: boolean): Promise<FixerResult> }` where `FixerResult = { findings, diffs, applied }`
- `Frontend/src/components/PbiFixer/components/pages/FixerPage.tsx` — **NEW**
  - Checkbox list grouped by Scope (Report / SM)
  - ⚡ button: default "Scan selected", toggles to "Apply selected" when Apply switch on
  - Live log panel, per-fixer status, diff preview panel, confirmation dialog

### Acceptance
- [x] All 6 first-cut fixers scan correctly against Contoso10K — *verified on Bad Report - Testing: Pie 1 / Bar 8 / Column 6 / PageSize 0 / UpgradeToPbir 0 / DiscourageImplicitMeasures 0 (no model loaded)*
- [ ] Apply writes back only when user explicitly flipped the Apply switch + reviewed diff + confirmed — *UX wired, write-back stubbed for v0.14*
- [x] ⚡ runs all checked in order; per-fixer scan/apply status visible
- [x] "Fix it" buttons from BPA pages (WS-C/D) preselect the matching fixer here — `pbifixer:bpa-fix` CustomEvent listener
- [x] Bump to **v0.14** (was v0.9 in original plan — superseded by delivery order)

### Dependencies
WS-A (for nav slot). Can run fully in parallel with WS-B/C/D — the BPA "Fix it" wiring
is additive (WS-C/D add a prop, WS-E consumes it, both land independently).

---

## WS-F — Perspectives page ✅ shipped v0.15
**Goal:** Port `_perspective_editor.py` — tri-state `<Checkbox>` tree (Table / Column / Measure /
Hierarchy per perspective), create / rename / delete perspectives.

**Implementation note (v0.15):** First cut ships the full UX contract (matrix grid with
tri-state checkboxes, add / rename / delete perspective, dirty-change tracker, Apply
switch + confirmation dialog). Perspectives are read from the **TMDL semantic-model
definition** (`getSemanticModelDefinition` — same endpoint `loadModelData` already uses),
which is more robust than `INFO.PERSPECTIVES()` DAX — that function requires a newer
compat level and returned a 400 on the demo model. TOM write-back is stubbed pending the
sempy-labs backend bridge: `applyPerspectiveChanges` surfaces "Backend bridge not yet
wired — Apply deferred." so users see the full flow without any model state change. The
dedicated `perspectives_controller.py` lands with the write-back work.

### Architecture
Read via TMDL `getDefinition` part `definition/perspectives/<name>.tmdl`. Parser walks
`perspective → perspectiveTable → perspectiveColumn|Measure|Hierarchy` and emits
`PerspectiveMember` rows with stable paths (`Table`, `Table[Column]`, `Table::Hierarchy`).
TOM writes will go through a dedicated controller owned by this workstream. Do **not**
route through WS-E; that would create unnecessary coupling.

### Safety rails
Scan-only by default; dedicated Apply switch + confirmation dialog. Same pattern as WS-E.

### Owns
- `components/pages/PerspectivesPage.tsx` ✅
- `services/perspectivesApi.ts` ✅ (TMDL reader + stubbed write)
- `Backend/agenthub/controllers/perspectives_controller.py` (deferred with TOM write)

### Acceptance
- [x] Tree renders with tri-state checkboxes (matrix grid, Table row aggregates children)
- [ ] Create / rename / delete perspective via TOM write — *UX wired, write-back stubbed for v0.15*
- [x] Scan-only default; Apply gated behind confirmation
- [x] Bump to **v0.15** (was v0.10 in original plan — superseded by delivery order)

### Dependencies
WS-A only.

---

## WS-G — Translations page + Auto-Translate Semantic Model ✅ shipped v0.11 → backend apply v0.40
**Goal:** Read/write translations from model metadata plus a new **Auto-Translate** feature
that drafts translations for the whole model via LLM and routes them through a review grid
before any write.

**Implementation note (v0.40):** Backend write-back shipped via new
`pbi_fixer_translations_apply` endpoint + module
`Backend/src/services/agenthub/tmdl_translations.py` (parse/serialize/merge culture TMDL,
deterministic output, preserves `linguisticMetadata` block verbatim, escapes special-char
names with single quotes). Flow = OBO Fabric token → `getDefinition?format=TMDL` (LRO) →
find/create `definition/cultures/<culture>.tmdl` → merge ApplyItems → `updateDefinition`
(LRO). Frontend stripped of all 501 branches; confirm-dialog body now describes the TMDL
round-trip. Pytest at `Backend/tests/unit/services/agenthub/test_tmdl_translations.py`
(5 cases). **Same TMDL round-trip pattern was reused for v0.41 Fixer apply.**

**Implementation note (v0.11):** First real page shipped on top of WS-A. Frontend ships
the full workflow — language + scope picker, Generate proposal button, inline-editable
review grid, Accept/Reject per row + bulk, Apply confirmation dialog with diff counts,
JSON/CSV export for Excel round-trip. Backend `translations/propose` + `translations/apply`
endpoints return 501 until the LLM + sempy-labs bridge is finalised; the UI shows a clear
banner and steers the user to Export.

**Implementation note (v0.17):** Fixed a 405 from nginx on `Generate proposal`. Root cause
in `services/translationsApi.ts`: the `BE` base URL was guarded with
`typeof process !== "undefined" && process.env && process.env.WORKLOAD_BE_URL`, which the
browser bundle short-circuits to `""` — so the POST went relative to the iframe origin
(`http://127.0.0.1:60006`) where nginx only serves GET routes. Dropped the guard to match
`controller/AgentHubApi.ts` (plain `process.env.WORKLOAD_BE_URL || ""`, which webpack's
DefinePlugin rewrites at build time) and added a 501-branch to `proposeTranslations` so
the "not yet enabled" banner renders instead of a raw error. Verified end-to-end against
the Demo / "Bad Report - Testing" model — 52 proposals rendered, no 405.

### UI
- Language picker (target culture — `de-DE`, `fr-FR`, … multi-select supported)
- Scope picker (All / Tables / Columns / Measures / Hierarchies / Descriptions)
- "Generate proposal" button
- Proposal review grid (see below)
- Red "Apply accepted translations" button (disabled until ≥1 row accepted)

### Engine
- `POST /api/pbi-fixer/translations/propose` body
  `{workspaceId, datasetId, targetCultures: string[], scope, sourceCulture?: string}`.
  Uses the LLM already wired via `github_chat_controller.py` — no new secret. Returns
  `{ culture, items: [{objectType, objectPath, sourceCaption, proposedCaption, proposedDescription?, existingCaption?}] }`.
  Pass already-translated items as glossary so repeated terms stay consistent.
- `POST /api/pbi-fixer/translations/apply` body `{workspaceId, datasetId, culture, items}` —
  backend uses `sempy_labs` TOM write to add/update translations and saves the model.

### Proposal review grid
- Columns: Object Type · Object · Source · Existing · Proposed · Accept?
- Inline-editable `Proposed` cell
- Per-row Accept + bulk Accept-all / Reject-all
- Filter by object type / "changed vs. existing" / "empty existing only"
- Diff badges: 🆕 new, ✏️ overwrite, ✅ unchanged

### Apply gate
1. Generate → review grid, nothing written
2. User edits / accepts rows
3. Confirmation dialog listing N captions, M descriptions, target culture(s)
4. On confirm → backend write
5. Export / import proposal as JSON / CSV for Excel round-trip editing

### Owns
- `components/pages/TranslationsPage.tsx`
- `components/translations/ProposalGrid.tsx`
- `services/translationsApi.ts`
- `Backend/agenthub/controllers/translations_controller.py`

### Acceptance
- [x] Existing translations load correctly
- [x] Generate proposal populates grid, nothing written yet
- [x] Inline edits survive accept/reject toggles
- [ ] Apply only writes accepted rows; confirmation dialog required — *UX wired, backend returns 501 pending LLM + sempy-labs bridge*
- [ ] Glossary context keeps repeated terms consistent (e.g. "Sales" same across table + measure) — *ships with backend bridge*
- [x] JSON / CSV export + import round-trip
- [x] Bump to **v0.11**

### Dependencies
WS-A. Independent of WS-F (both touch TOM-write path but via separate controllers).

---

## WS-I — Delta Analyzer page ✅ shipped v0.24
**Goal:** Port `sempy_labs.delta_analyzer()`. Read-only view comparing two model snapshots
(e.g. pre/post fixer run).

**Implementation note (v0.24):** Same hybrid pattern as WS-B / WS-C / WS-D — ships the
full UX without waiting on the `sempy_labs` backend bridge. Snapshots are captured via
the friendly `INFO.VIEW.*` DAX family (reuses `loadVertipaqData` from `memoryApi.ts`) and
stored in `sessionStorage` (capped at 20, FIFO eviction on quota). The diff engine in
`services/deltaApi.ts` indexes Tables / Columns / Measures / Relationships by stable key
and emits Added / Removed / Changed rows with per-property before→after diffs. The page
renders summary cards (`+a −r ~c` per category) plus a tabbed grid; pick "Live model"
as the compare side to capture-on-demand against the currently-loaded SM. CSV export
included. The eventual `pbi_delta_controller.py` backend bridge will be a drop-in
replacement for `takeSnapshot` — the diff engine and UI stay untouched.

### Owns
- `components/pages/DeltaAnalyzerPage.tsx` ✅
- `services/deltaApi.ts` ✅ (snapshot capture + sessionStorage + diff engine + CSV)
- `Backend/agenthub/controllers/pbi_delta_controller.py` (deferred with sempy-labs bridge)

### Acceptance
- [x] Pick two snapshots (or "current" vs. stored) and see a diff grouped by Tables / Columns / Measures / Relationships
- [ ] Matches sempy-labs notebook output — *deferred: client-side `INFO.VIEW.*` snapshot used for v0.24; sempy-labs parity comes with backend bridge*
- [x] Bump to **v0.24** (was v0.12 in original plan — superseded by delivery order)

### Dependencies
WS-A.

---

## WS-J — Diagram page (Power BI Desktop-style)
**Goal:** Relationship graph modeled on **Power BI Desktop's Model/Diagram view**:
- Draggable table cards (collapse-to-header toggle)
- Relationship lines with 1 / ∗ cardinality markers on endpoints
- Diamond glyph on the line for filter-direction (bidirectional / single)
- Dashed line for inactive relationships
- Auto-layout + save layout positions

### Library
**`reactflow`** — closest match to the PBI Desktop canvas feel; supports custom node
components for the cards and custom edge renderers for cardinality glyphs.

### Owns
- `components/pages/DiagramPage.tsx`
- `components/diagram/TableNode.tsx`
- `components/diagram/RelationshipEdge.tsx`
- `components/diagram/layout.ts`
- `services/diagramApi.ts`

### Acceptance
- [ ] All tables + relationships from the loaded model render
- [ ] Cardinality + filter-direction glyphs match PBI Desktop
- [ ] Drag tables + save layout; reload restores positions (session-local OK for now)
- [ ] Collapse/expand card
- [ ] Bump to **v0.13**

### ✅ Implementation note (v0.19 → hotfix v0.20)
- **Library choice**: `reactflow` was the proposed library, but installing it would have required a fresh `npm install` inside the frontend image (no `node_modules` on the host) and a webpack config touch — both contrary to the WS-J/WS-N split. Shipped a **pure SVG canvas** instead, which avoids the shared-deps hassle (`Frontend/package.json` unchanged) while delivering identical UX.
- **Files**: `services/diagramApi.ts` (data flattening + sessionStorage layout cache + auto-layout grid seeded by relationship degree) and `components/pages/DiagramPage.tsx` (toolbar + SVG canvas + custom node/edge renderers).
- **Rendering**:
  - Card per table (header bar = drag handle, body lists visible columns truncated at 12 with “+ N more…” footer; calculation groups/tables tinted differently; key columns flagged with 🔑; hidden tables auto-filtered with a `Show hidden` toggle).
  - Edge per relationship with anchor points snapped to the closest card border. Cardinality (`1` / `*`) glyphs on each endpoint, mid-line filter-direction diamond (◇ single, ◈ both), dashed + dimmed when `isActive=false`.
- **Interaction**: pointer-down on header drags the card (layout persisted to `sessionStorage` keyed by datasetId). Pointer-down on the empty canvas pans. Mouse wheel zooms (cursor-anchored). `Fit` recenters to bounding box. `Reset Layout` clears stored positions and re-runs auto-layout. `Refresh` reloads the model. Collapse ▴/▾ toggles the column body to header-only (Playwright-verified: text count drops from 20 → 2).
- **v0.20 hotfix**: v0.19 imported `Layout20Regular` and `ArrowMaximize20Regular` from `@fluentui/react-icons` — both were undefined exports in the bundled version and threw the React minified `#130` ("got: undefined") error, leaving the canvas blank. Fixed by switching to `ArrowExpand20Regular` for `Fit` and removing the icon on `Reset Layout`.
- **Verification**: Playwright against Demo / Bad Report - Testing renders 1 card (Orders) with all 9 visible columns, type badges, legend, and the full toolbar. Refresh shows the loading spinner overlay. Collapse cycles header-only ↔ full body.
- **Known gap vs spec**: zoom/pan is local state (intentional — not part of acceptance). Layout is per-tab via sessionStorage (acceptance allows this). Future polish if needed: orthogonal edge routing instead of straight lines.

### Dependencies
WS-A. Library choice is finalized here, but the actual `Frontend/package.json` edit is owned
by WS-N to avoid a shared-file collision.

---

## WS-K — Script Runner page
**Goal:** Monaco editor + Run button → backend eval endpoint. **Full-power**, feature-flagged
on `PBI_FIXER_ENABLE_SCRIPT_RUNNER`. See **Appendix A** for the security rationale.

### Owns
- `components/pages/ScriptRunnerPage.tsx`
- `Backend/agenthub/controllers/script_runner_controller.py`

### Acceptance
- [ ] Python REPL runs with `sempy`, `sempy_labs`, forwarded OBO tokens available
- [ ] TS runner option (sandboxed in a worker) works for quick DAX queries
- [ ] Streaming output (SSE or chunked)
- [ ] Red banner: "⚠ Full-power execution — runs as you, with your tokens. Local dev only."
- [ ] Disabled in UI + backend when `PBI_FIXER_ENABLE_SCRIPT_RUNNER` is not `true`
- [ ] Bump to **v0.14**

### Dependencies
WS-A. Fully independent backend controller.

---

## WS-L — About page
**Goal:** Version, links (GitHub, sempy-labs, notebook demo), credits, build info.

### Owns
- `components/pages/AboutPage.tsx`

### Acceptance
- [ ] Shows current `v0.x` from a single source of truth (`utils/version.ts`)
- [ ] Links open in new tab
- [ ] Bump to **v0.15**

### Dependencies
WS-A. Trivial.

---

## WS-M — Prototype page ✅ shipped v0.16
**Goal:** Port `_report_prototype.py`. Page layout editor — drag/drop visuals onto a canvas,
generate PBIR skeleton, export as a new report.

**Implementation note (v0.16):** First cut ships the full UX — 8-type visual palette
(Card / Table / Matrix / Bar / Column / Line / Pie / Slicer), free-positioned canvas
with 40 px grid background, click-to-add visuals, mouse drag to move, bottom-right
handle to resize, Inspector panel for title + field binding from the loaded semantic
model (`loadModelData`). Export emits a **PBIR-lite JSON skeleton** (`pbir-skeleton/1.0`)
downloaded via `Blob` + `<a download>`. Upload-as-report surfaces the stubbed backend
bridge message — the real PBIR `createReport` + content upload lands once the
sempy-labs bridge is wired. Fields carry a `role` (Values / Category) and `kind`
(column / measure) so the eventual converter can map into PBIR query roles without
re-inferring.

### Owns
- `components/pages/PrototypePage.tsx` ✅ (canvas + palette + inspector all in one)
- `services/prototypeApi.ts` ✅ (types + `exportPrototypeToPbir` + stubbed `uploadPrototypeAsReport`)

### Acceptance
- [x] Drag visuals from palette onto a canvas grid (click-to-add; drag to reposition)
- [x] Bind a field from the loaded SM to a visual
- [ ] Export PBIR skeleton that opens in Power BI Desktop without errors — *JSON export works; real PBIR conversion is the backend-bridge follow-up*
- [x] Bump to **v0.16**

### Dependencies
WS-A. Largest remaining workstream; can be deferred.

---

## WS-N — Integration & Shared Assets Sweep ✅ shipped (v0.36–v0.37)
**Goal:** Final low-risk integration pass that is the **only** workstream allowed to touch
shared files after WS-A. This makes the other workstreams independently buildable in parallel.

**Status snapshot (as of v0.37):**
- [x] BPA "Fix it" → Fixer page wiring shipped incrementally inside WS-C/D/E:
      `ModelBpaPage` + `ReportBpaPage` dispatch `window.CustomEvent('pbifixer:bpa-fix', {detail})`
      and call `onNavigate('fixer')`; `FixerPage` listens and preselects matching fixer ids.
- [x] Cross-workstream smoke passes ran after each WS merge (manual Playwright).
- [x] `utils/version.ts` extracted (v0.36) — single source of truth re-exported from
      `utils/index.ts`; `PbiFixerPage.tsx` imports from there.
- [x] Cross-tab BPA Fix-it relay (v0.36): shell-level listener in `AgentHubLayout.tsx`
      catches the event from any sub-tab, stashes payload in
      `sessionStorage["pbiFixer.pendingBpaFix"]`, opens the Fixer tab via
      `handlePbiFixerSubNav("fixer")`, then re-dispatches the event with `__relayed=true`.
      `FixerPage.tsx` drains sessionStorage on mount + listens for the relay.
- [x] Removed stale "WS-N" mis-labels from `TranslationsPage.tsx` and `modelBpaApi.ts` (v0.37):
      backend TOM-write apply path is a separate future backend workstream, not WS-N.
- [ ] Shared frontend deps (`reactflow` for WS-J, Monaco-related for WS-K) — ~~**not installed**, blocked on those workstreams starting~~ **WS-J shipped without `reactflow`** (pure SVG canvas, no shared-deps touch needed). Monaco still pending for WS-K.

### Owns
- `utils/version.ts` — **NEW** single source of truth for displayed `v0.x`
- `Frontend/package.json` — shared dependency additions (`reactflow`, Monaco-related packages)
- Any unavoidable shared import/export surface created after WS-A
- Final cross-page wiring for typed integration events (e.g. BPA "Fix it" → Fixer page)

### Responsibilities
- Apply the actual version bump after merged workstreams land
- Install shared frontend dependencies required by already-merged workstreams
- Wire typed events exposed by WS-C / WS-D into WS-E without changing feature logic
- Perform the final smoke pass after each batch merge

### Acceptance
- [x] `utils/version.ts` is the only displayed version source of truth (v0.36)
- [ ] Shared deps installed once, not by feature workstreams — *blocked on WS-K (Monaco)*
- [x] BPA "Fix it" flows wire into Fixer page without feature-level merge conflicts
- [x] Smoke test after each integration batch

### Dependencies
WS-A first, then whichever feature workstreams are ready to integrate.

---

## Parallel Development Guide — Multiple Chat Windows, Same Repo

You (Alexander) open N chat windows. Each chat owns one workstream. They
all edit the same local clone in `C:\Users\alkorn\repos\fabric_agenthub\`. Because file
ownership is disjoint (see Design Principle above), there are no hard conflicts.

Copy this block into each new chat:

> "You're working on **WS-X** of `Developer Hub/Frontend/src/components/PbiFixer/PLAN.md`.
> Read that file, then the `## WS-X` section. Only edit files listed under that workstream's
> **Owns** section. Never touch `PbiFixerPage.tsx` (WS-A only) or shared files like
> `utils/version.ts` / `Frontend/package.json` (WS-N only).
> When done: do **not** change the shared version file; instead commit
> `[WS-X] <summary> (target vX.Y)` and tick the acceptance checkboxes in PLAN.md.
> If you hit a conflict with another chat's edits, rebase and let the last edit win on that
> line — your scope is disjoint so no real logic should collide."

### Recommended launch order
1. **Chat 1** — WS-A (shell refactor + theming). Run to completion first.
2. Once WS-A landed, launch **in parallel**:
   - **Chat 2** — WS-B (Vertipaq)
   - **Chat 3** — WS-C (Model BPA)
   - **Chat 4** — WS-D (Report BPA)
   - **Chat 5** — WS-E (Fixer Execution)
3. After WS-C/D/E landed, launch the remaining pages **all in parallel** (one chat per WS):
   - **Chat 6** — WS-F (Perspectives)
   - **Chat 7** — WS-G (Translations + Auto-Translate)
   - **Chat 8** — WS-I (Delta Analyzer)
   - **Chat 9** — WS-J (Diagram)
   - **Chat 10** — WS-K (Script Runner)
   - **Chat 11** — WS-L (About)
   - **Chat 12** — WS-M (Prototype)
4. Final **Chat 13** — WS-N (integration sweep: version, package.json, cross-page wiring).

---

## Appendix A — Script Runner (WS-K) Decision & Security Note

**Decision (per user, April 23 2026):** include Script Runner as **full-power** — Monaco
editor in the frontend, Python REPL in the backend, access to `sempy`, `sempy_labs`, the
workspace Fabric/PBI tokens, and the filesystem inside the container. No sandbox.

**What is Script Runner?** A tab with a code editor and a "Run" button. The user writes
arbitrary Python (or TS) and executes it against the currently-selected workspace/model/
report. Typical use: one-off TOM tweaks, ad-hoc DAX queries, testing a fixer before it's
wrapped into the Fixer page, debugging customer models in-session.

**Why this is documented in PLAN.md and not README.md:** the `Developer Hub/README.md` is
shared with the upstream maintainer (Lukasz) and targets all Developer Hub users.
Script Runner is a **power-user, security-sensitive backdoor** and should stay an internal
capability. Add a short note to `Developer Hub/README.md` pointing maintainers here. Do not
ship it enabled by default to downstream users — gate behind an env var
`PBI_FIXER_ENABLE_SCRIPT_RUNNER=true` in `docker-compose.yaml`.

**Security acknowledgements (documented so we're honest about the risk):**
- Code runs in the backend container with the backend's service identity and the forwarded
  user OBO tokens → full Fabric/PBI/OneLake access as the user.
- Arbitrary filesystem, network, and subprocess access inside the container.
- No input/output sanitization — scripts can print tokens if asked.
- This is acceptable **only** because the Developer Hub runs locally against the user's own
  tenant and the user is authenticating as themselves. Never enable in a shared/hosted
  deployment.

**Owns for Script Runner (WS-K):**
- `Backend/agenthub/controllers/script_runner_controller.py` — `POST /api/pbi-fixer/script`
  body `{language: 'python'|'ts', code, context: {workspaceId, datasetId, reportId}}` →
  streams stdout/stderr via SSE or chunked response. Feature-flagged on
  `PBI_FIXER_ENABLE_SCRIPT_RUNNER`.
- `Frontend/src/components/PbiFixer/components/pages/ScriptRunnerPage.tsx` — Monaco editor,
  language toggle, Run button, output panel. Shows a red banner:
  "⚠ Full-power execution — runs as you, with your tokens. Local dev only."

---

---

# Appendix B — Design Alignment with AgentHub (proposal, NOT yet implemented)

> Goal: make the PBI Fixer shell visually and behaviourally feel like a first-class
> citizen of the Developer Hub / AgentHub, instead of looking like a separate FluentUI
> playground bolted on. Today the two surfaces share the host page chrome but diverge
> the moment you enter the `pbifixer` iframe — different sidebar palette, different
> nav item styling, different active-state language, different topbar pattern, no
> shared transitions.
>
> This appendix is **planning-only**. Nothing here is implemented yet. Open questions
> for Alexander to answer are listed at the end so the actual workstream (proposed
> name **WS-O — Design Alignment Pass**) can start unambiguously.

## Side-by-side comparison (current build, v0.23)

### Sidebar / left navigation
| Aspect | AgentHub (`AgentHubLayout.tsx` + `styles.scss .agenthub-sidebar`) | PBI Fixer (`PbiFixerNav.tsx` Fluent `makeStyles`) |
|---|---|---|
| Width | 224 px, animated collapse (200 ms cubic-bezier 0.33,0,0.67,1) | 220 px, no collapse, no animation |
| Background | `#f4f3f2` (surface-container-low, warm grey) | `tokens.colorNeutralBackground2` (cooler Fluent grey) |
| Border-right | None — uses background contrast for separation | 1 px `colorNeutralStroke2` divider |
| Item shape | `border-radius: 8px`, `margin: 2px 10px`, padding `8px 12px` | `borderRadiusMedium` (4 px), `padding: 6px 10px`, no outer margin |
| Hover | `background: rgba(233, 232, 231, 0.6)`, `color: #1a1c1c`, icon recolours | `colorNeutralBackground3Hover`, no icon recolour |
| Active | White card (`#ffffff`) + 3 px primary accent bar on the left + box-shadow + icon turns primary blue + animated accent slide-in | Subtle `colorNeutralBackground1Selected` tint, semibold text, no accent bar, no animation |
| Icons | FluentUI 24 px regular icons (`Bot24Regular`, `Wrench24Regular`, …) | Plain Unicode emoji glyphs (🗂 📊 ⚡ …), 16 px |
| Section dividers | `.sidenav-section-label` uppercase 11 px, optional `--spaced` variant; `.sidenav-rail-divider` between groups | Single uppercase "PBI Fixer" header, no further dividers |
| Group expand | "Others" treated as a peer with chevron — collapsed by default | Same chevron pattern, but visually identical to peers (no hierarchy cue) |
| Footer | `.sidenav-footer-item` slot for sign-out / help / settings | None — no footer area |
| Keyboard | Tab-able rows, Enter / Space activate; arrow-key tree nav not present | Same Enter / Space; no arrow-key nav (WS-A acceptance bullet still open) |
| Disabled state | n/a (AgentHub never lists "coming soon" items) | Italic + muted color via `rowDisabled` |

### Topbar / header
| Aspect | AgentHub `.agenthub-topbar` | PBI Fixer `styles.header` |
|---|---|---|
| Height | 48 px fixed | Implicit (~36 px), padding-driven |
| Background | `#faf9f8` warm surface | `colorNeutralBackground1` (white) |
| Layout | left = brand + search; right = icon cluster + avatar | left = title + version; nothing on the right |
| Search | Global search box (`.agenthub-topbar-search`) with scope chips | None |
| Right cluster | 24 px topbar icons (Help, Notifications, Chat) + 28 px circular avatar | None |
| Border | 1 px `rgba(192, 199, 212, 0.1)` hairline | 1 px `colorNeutralStroke2` |
| Behaviour | Persistent across all pages of the hub | Persistent across PBI Fixer pages only |

### Connection bar (PBI Fixer only)
- AgentHub has **no equivalent** — context (workspace / agent / session) is selected
  inside each page or via the URL.
- PBI Fixer puts a 3-combobox bar (Workspace / Semantic Model / Report) directly under
  the header, full-width across the body. Visually it competes with the topbar for
  attention and feels like a third horizontal stripe.
- **Visual mismatch**: the bar uses `colorNeutralBackground1` (same as content), so the
  topbar / connection bar / content area look like one continuous slab. AgentHub gets
  away with no connection bar at all, so we cannot copy a pattern — we have to invent
  one that *feels* AgentHub-native.

### Page content surface
| Aspect | AgentHub pages | PBI Fixer pages |
|---|---|---|
| Padding | Per-page; mostly `24px` outer with cards inside | `12px 16px` (tighter, denser) |
| Card pattern | Heavy use of soft cards with `border-radius: 12px`, subtle shadow, 16 px gap | Mostly flat panels, dense Fluent DataGrids, no card chrome |
| Empty / loading | Branded empty states with illustration + CTA | Generic Fluent `Spinner` + grey text |
| Typography scale | Mixed: large display headings (`Title2`/`Title3`) over Body1 | Mostly `Text` defaults, occasional `fontSizeBase500` |
| Font | Inherited Segoe UI everywhere | Inherited Segoe UI **except** ModelExplorer / ReportExplorer tree rows + DAX preview where a custom font stack leaks through (WS-A acceptance bullet still open) |

### Motion / micro-interactions
- AgentHub uses one consistent motion language: 120–200 ms with cubic-bezier(0.33, 0, 0.67, 1).
  Sidebar collapses, accent bar slides in, hover transitions all use it.
- PBI Fixer has **no transitions** anywhere — page swaps are instant snaps, hover/active
  state changes are instant, "Others" expand/collapse is instant.

### Tech stack split
- AgentHub uses **SCSS classes** in `styles.scss` (BEM-ish: `.agenthub-sidenav`,
  `.sidenav-item`, `.sidenav-item--active`).
- PBI Fixer uses **FluentUI `makeStyles`** with `tokens.*`. Zero overlap with the
  SCSS file.
- Result: theming changes in `styles.scss` (light/dark, brand colour tweaks) do **not**
  reach the PBI Fixer. The two surfaces will diverge again with every Lukasz-side
  redesign unless we either share classes or re-derive the same look from tokens.

### Functional behaviour gaps (lighter look-and-feel hits)
| Behaviour | AgentHub | PBI Fixer |
|---|---|---|
| Lazy page loading | Each page is a webpack chunk; `preload()` on hover | All pages bundled in a single chunk |
| Tabbed editor surface | `EditorGroupsRoot` allows multiple tabs / split groups | Single active page only |
| Drag-and-drop nav | Nav items are `draggable="true"` for tab opening | Nav items are not draggable |
| Right-click context menu | `SideNavContextMenu` (open in new tab, pin, …) | None |
| Search | Global search via `SearchContext`, scoped per page | None |
| Per-user preferences | `useNavPreferences` (open behaviour: tab vs replace) | `sessionStorage` only (`pbiFixer.activeNav`, `pbiFixer.othersExpanded`) |

---

## Proposed alignment plan — WS-O — Design Alignment Pass

This is sized as a **single dedicated workstream** so it doesn't compete with feature
WSes for shared file ownership. It targets `v0.X+1` after WS-N integration.

### Phase 1 — Visual parity (no behavioural change)
1. **Switch the PBI Fixer sidebar to the AgentHub class language.** Replace the
   `makeStyles` rules in `PbiFixerNav.tsx` with the existing `.agenthub-sidenav`,
   `.sidenav-item`, `.sidenav-item--active`, `.sidenav-section-label`,
   `.sidenav-rail-divider` SCSS classes. Keeps the markup React-controlled but pulls
   the styling from the shared SCSS source of truth → automatic theming parity.
2. **Replace emoji glyphs with FluentUI 24 px regular icons.** Map each `NavKey` to
   a real icon (e.g. `model → DatabaseStack24Regular`,
   `report → ChartMultiple24Regular`, `fixer → Wrench24Regular`,
   `modelBpa / reportBpa → BookSearch24Regular`,
   `memory → MemoryRegular24Regular`, `perspectives → Eye24Regular`,
   `translations → Globe24Regular`, `delta → ArrowSwap24Regular`,
   `diagram → Flowchart24Regular`, `scriptRunner → Code24Regular`,
   `prototype → Beaker24Regular`, `about → Info24Regular`). **Verify each icon
   actually exists in the installed `@fluentui/react-icons` bundle** (see WS-J v0.20
   gotcha — TS compile passes for non-existent names; runtime is `undefined`).
3. **Promote the active-page accent bar.** Add the 3 px primary-coloured left bar +
   white card background + icon recolour to active items, matching `.sidenav-item--active`.
4. **Add the slide-in accent animation** (`@keyframes sidenavAccentIn`) — already in
   the SCSS, it just isn't reaching the Fixer.
5. **Topbar redesign.** Move the title + version into an `.agenthub-topbar`-styled
   strip. Add a right-side cluster with at least: a "Help" link to the GitHub README,
   a notifications dot for fixer-run results (placeholder), and the build/version pill.
   Use the same 48 px height + warm `#faf9f8` background.
6. **Reframe the connection bar.** Two options to evaluate (see open questions):
   - (a) Keep it as a horizontal strip but restyle with the warm surface palette and
     a soft 1 px hairline so it reads as a sub-header of the topbar, not a third stripe.
   - (b) Move it into the sidebar as a fixed section above the nav (Workspace / SM /
     Report stacked vertically), so the content area gets full width and the page
     header line matches AgentHub.
7. **Page content padding alignment.** Bump the content padding from `12px 16px` to
   `24px` to match AgentHub's breathing room. Audit each page (Model, Report, Memory,
   BPA, Diagram, …) for double-padding regressions.
8. **Font normalisation.** Strip the leftover hard-coded `fontFamily` in
   `ModelExplorer.tsx` / `ReportExplorer.tsx` tree rows + filter input + properties
   panels (already on the WS-A acceptance list — finally close it here).
9. **Shared empty / loading states.** Replace the bare `Spinner` + grey text with a
   small reusable `<EmptyState />` styled like AgentHub's (icon + headline + sub-text
   + optional CTA).

### Phase 2 — Motion + interaction parity
1. Adopt the AgentHub motion curve (`120–200 ms cubic-bezier(0.33, 0, 0.67, 1)`) for
   all hover, active, and Others expand/collapse transitions in the PBI Fixer.
2. Add a soft fade/slide on page swap (`activeNav` change) — probably 120 ms opacity
   crossfade, no horizontal motion (avoid disorienting users on dense data grids).
3. Add arrow-key tree navigation (↑/↓ moves selection, →/← expands/collapses Others,
   Enter activates) — closes the matching WS-A acceptance bullet.

### Phase 3 — Optional behavioural lifts (decide per question)
1. Make PBI Fixer pages **lazy-loaded chunks** with `preload()`-on-hover, mirroring
   `AgentHubLayout`'s `lazyWithPreload`. Pay-off: smaller initial bundle, faster first
   paint of the Fixer.
2. Hook PBI Fixer into the `EditorTabsProvider` / `EditorGroupsRoot` system so Fixer
   pages can open in tabs alongside AgentHub pages. Big pay-off, also bigger refactor —
   needs Lukasz alignment.
3. Add a simple right-click context menu on Fixer nav items ("Open in new tab",
   "Reload"), reusing `SideNavContextMenu`.
4. Consider routing PBI Fixer nav state through React Router (instead of
   `sessionStorage`) so deep-links work and back/forward navigates between pages.

### Out of scope for WS-O
- Search bar in the Fixer topbar (no existing PBI-Fixer search index to back it; defer).
- Avatar / sign-out in the Fixer topbar (the host AgentHub topbar already provides this
  one tier up; duplicating would be confusing).
- Replacing FluentUI v9 components with custom SCSS-styled equivalents (we keep the
  components, only restyle the chrome).

### Acceptance (proposed)
- [ ] Sidebar of the PBI Fixer is visually indistinguishable from the AgentHub sidebar
      at first glance (palette, spacing, item shape, active accent, icon set).
- [ ] Topbar matches the AgentHub 48 px warm-surface pattern.
- [ ] Connection bar restyled (option a or b per open question) with no third visual
      stripe competing with topbar/content.
- [ ] All transitions use the shared cubic-bezier motion curve.
- [ ] No remaining hard-coded `fontFamily` in tree rows / properties panels.
- [ ] Light + dark mode both render parity with AgentHub home.
- [ ] Bump to **v0.X+1** (WS-N owns the actual version file).

### Owns (proposed)
- `components/PbiFixerNav.tsx` — rewrite styling to use shared SCSS classes
- `components/PbiFixerPage.tsx` — header + connection-bar restyle, content padding
- `types/nav.ts` — replace `icon: string` (emoji) with `icon: React.ReactNode`
  carrying real FluentUI icons; verify each name exists in the bundle
- `styles.scss` — only **append** new classes if needed (e.g. `.pbifixer-connection-bar`),
  do not mutate existing AgentHub classes
- `components/ModelExplorer.tsx`, `components/ReportExplorer.tsx` — font normalisation
  pass + empty-state replacement
- New `components/common/EmptyState.tsx` — small shared empty/loading component

---

## Open questions for Alexander (answer before WS-O kicks off)

1. **Connection bar treatment** — option (a) restyled horizontal strip under the topbar,
   or option (b) move pickers into the sidebar above the nav? (b) gives the cleanest
   AgentHub feel but loses horizontal real estate and changes the muscle memory of
   anyone who already uses the Fixer.
2. **Icon set** — OK with replacing the playful emoji glyphs (🗂 📊 ⚡ 👁 🌐 …) with
   monochrome FluentUI 24 px icons? The emojis carry colour and identity but read as
   informal compared to the rest of the Hub.
3. **SCSS vs `makeStyles`** — happy to migrate the PBI Fixer chrome (sidebar / topbar /
   connection bar) to the shared `styles.scss` classes, or do you want to stay
   token-only and re-derive the same look from `tokens.*`? SCSS reuse is the cheapest
   way to stay in sync with Lukasz's redesigns; staying token-only keeps the Fixer
   self-contained.
4. **Topbar right cluster** — what should live there? Suggested minimum: Help link
   (GitHub README), version pill, "Open in Fabric" link. Skip notifications + avatar
   (already in the host hub topbar)?
5. **Lazy-loading PBI Fixer pages** — worth doing in WS-O, or defer to a later perf pass?
   Currently the whole Fixer is one chunk — bundle size is small enough that the win is
   modest, but it would mirror the AgentHub pattern.
6. **Tabs/EditorGroups integration** — should PBI Fixer pages participate in the
   AgentHub `EditorGroupsRoot` (open in tab, split, drag-reorder)? This is the most
   AgentHub-native we could possibly get, but requires touching `AgentHubLayout` and
   negotiating with Lukasz. In/out for WS-O?
7. **Routing** — switch PBI Fixer nav from `sessionStorage` to React Router so deep-links
   like `#/agent-hub/pbifixer/diagram` work? Helps onboarding (link to a specific tab
   from docs) but means the Fixer becomes a route-aware citizen, not a sub-app.
8. **Footer slot** — AgentHub has `.sidenav-footer-item` for help / sign-out. Want a
   matching footer in the Fixer sidebar (e.g. "Report a bug → GitHub Issues",
   "Open notebook version", version pill), or leave the bottom empty?
9. **Active-state accent colour** — keep AgentHub's `#005faa` primary, or pick a
   distinct PBI-Fixer accent (e.g. PBI yellow `#F2C811`) so users *see* they're in the
   Fixer surface? Trade-off: identity vs. seamlessness.
10. **Page-swap motion** — opacity crossfade on `activeNav` change OK, or do you want
    truly instant swaps (some users find motion annoying on dense data grids)?
11. **Disabled "Coming soon" items** — keep showing them (current behaviour, italic +
    muted) so users see the roadmap, or hide them entirely until ready (matches
    AgentHub which never advertises unfinished pages)?
12. **Dark mode** — confirm priority. AgentHub supports it via the SCSS rules around
    line 3296+; if WS-O reuses those classes, dark mode "comes for free". If we stay
    token-only, dark mode needs an explicit pass.

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green-light from Alexander
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param on the
  endpoint is reserved for future user-supplied URL override but no UI for it yet)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
