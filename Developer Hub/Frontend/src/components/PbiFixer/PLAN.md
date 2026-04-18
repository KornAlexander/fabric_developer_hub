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
| 📈 Vertipaq | `_pbi_fixer.py` (inline) | ~300 | P2 | Medium | Vertipaq Analyzer — table/column sizes, compression stats |
| 💾 Memory | `_pbi_fixer.py` (inline) | ~200 | P3 | Low | Memory analysis tab |
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
