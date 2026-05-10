# PBI Fixer — TypeScript Port Plan

> Forward-looking work only. For shipped workstream history, see [CHANGELOG.md](CHANGELOG.md).
>
> **Source:** `c:\Users\alkorn\repos\pbi_fixer\src\` (Python notebook, ~12,000 lines)
> **Target:** `c:\Users\alkorn\repos\Fabric_Developer_Hub\Developer Hub\Frontend\src\components\PbiFixer\`
> **Version policy:** stay on `v0.x`. Bump patches (`v0.5` … `v0.99`). **Never auto-bump to `v1.0`** — Alexander gives explicit green light.

---

## Top priority list (short form)

| Rank | Item | WS | Status | Why |
|------|------|----|--------|-----|
| **1** | **IBCS Variance + error bars + % value alignment** | WS-E-IBCS step #6 | 🟡 V1 shipped (PBIR-only; sidecar full port deferred — v0.103) | Signature feature, 771-line fixer, includes red `#FF0000` error bars and Δ PY % data-label alignment that the user explicitly asks for |
| **2** | **Sidecar bring-up + Add Measure Table** | WS-E-IBCS step #1 | ⬜ blocked on `PBI_API_TOKEN` XMLA-write audience verification | Unblocks every IBCS fixer. **First validation must be: does the existing OBO token write to XMLA?** |
| **3** | **PY Measures + Calendar + Theme + Fix All Charts** | WS-E-IBCS steps #2-#5 | ⬜ | Prerequisites for the variance fixer; sequential ship after #2 lands |
| **4** | **One-click "Apply IBCS" macro** | WS-E-IBCS step #7 | 🟡 V1 shipped (no transactional rollback — v0.103) | The differentiator vs other PBI tooling — runs steps 1–6 transactionally |
| **5** | **WS-E-TEMPLATES — community TMDL templates** | WS-E-TEMPLATES | ⬜ | 7 fixers (Calendar / Measure-org / Last-refresh / Model-doc), each is small, no sidecar dependency |
| **6** | **WS-LOCAL — Local PBIP/PBIX bridge (Seed only)** | WS-LOCAL step #1 | ⬜ | "Save edits to disk" — single button, validates File System Access API in Fabric iframe |
| **7** | **WS-MON — Workspace Monitoring wizard** | WS-MON | ⬜ | First admin-deploy wizard, shakes out the multi-step pattern reused by FUAM/FCA/USAGE |
| **8** | **WS-USAGE-A — Usage Metrics notebook download** | WS-USAGE-A | ⬜ | Cheapest possible win, single button serving the pinned notebook |
| **9** | **WS-FUAM / WS-FCA / WS-USAGE-B+C** | WS-FUAM, WS-FCA, WS-USAGE | ⬜ | Heavier admin wizards, ship after WS-MON proves the pattern |
| **10** | **Remaining ported fixers (P2/P3 backlog)** | WS-E backlog | ⬜ | TrimObjectNames, CapitalizeObjectNames, MigrateReportLevelMeasures, MigrateSlicerToSlicerbar, MonthColumnFormat, SortMonthColumn, FlagColumnFormat, IsAvailableInMdxTrue, ColumnToLine, PrepForAI, CacheWarming, IncrementalRefresh — see "Remaining fixers" section below for full table |

**Parity snapshot vs python source** ([PBI-Fixer/pbi_fixer/src/](../../../../../../../PBI-Fixer/pbi_fixer/src/)): 26 of 41 `_Fix_*` / `_Add_*` scripts ported (~63%). Notable **missing**: `Fix_IBCSVariance` (the big one — error bars + % alignment), all 5 `Add_CalcGroup_*` / `Add_CalculatedTable_*` / `Add_PYMeasures` / `Add_PrepForAI` / `Add_IncrementalRefresh` / `Add_CacheWarming`, plus the polish-tier `Fix_Trim/Capitalize/MonthColumn/SortMonth/Flag/IsAvailableInMdxTrue/MigrateReportLevelMeasures/MigrateSlicerToSlicerbar`. Full per-script gap table in "Remaining fixers (WS-E backlog)" below.

---

## Delivery status snapshot

Shipped workstreams (WS-A, WS-C, WS-D, WS-E, WS-F, WS-G, WS-I, WS-J, WS-L, WS-M, WS-O, WS-Q, plus Explorer parity batches v0.59 + v0.60) live in [CHANGELOG.md](CHANGELOG.md). Open work below.

| WS | Feature | Status | Version |
|----|---------|--------|---------|
| WS-B | Memory Analyzer | 🟡 partial (Phase 2) | v0.18 |
| WS-N | Integration sweep | 🟡 partial | v0.36–v0.37 |

Legend: 🟡 partial • ⬜ not started • 📋 proposed (open questions)

**Deferred work across shipped WSes** (require sempy-labs/AMO/XMLA backend bridge):
- WS-B: per-column storage stats, Partitions, Hierarchies
- WS-C/D: real sempy-labs rule parity (currently client-side rules)
- WS-F: TOM write-back (Apply currently surfaces deferred message)
- WS-I: real sempy-labs `delta_analyzer` parity (currently client-side `INFO.VIEW.*` snapshots)
- WS-M: real PBIR `createReport` (currently exports JSON skeleton only)
- WS-E: `Fix_UpgradeToPbir` (needs sempy-labs runtime)

---

## Open bugs / UX tasks (next steps)

Tracked here so they're not lost between sessions. Several are AgentHub-shell scope, not just PBI Fixer — call them out and coordinate with the AgentHub workstream where noted.

### PBI Fixer — bugs
- **B1. Empty Description on items.** Allow item creation / edit with an empty Description field. Backend / `ItemContext.createItem` already passes `description || ""`, so the empty path is supported in code — likely a UI validation in the create dialog. Verify in the actual create dialog (Fabric host or in-workload).
- **B2. Placeholder below the Fixer.** Stray placeholder element renders below the Fixer panel — delete it.
- **B3. Close button.** Test the close button in the Fixer view. If it does not actually close the surface (or behaves inconsistently with other AgentHub items), wire it up. If the button is non-functional and not needed, remove it instead.
### AgentHub-shell — bugs (coordinate with WS-O / Lukasz)
- **B5. GitHub sign-in greys out the hub.** When signing in with GitHub on a "create item" task in Developer Hub, the whole hub greys out and only recovers after a full page refresh. Reproduce the flow (create item → sign into GitHub → verify hub stays interactive). Likely a missing post-auth message handler or modal-overlay state that doesn't get cleared on the OAuth popup return.
- **B6. SSO prompted per tab.** Each new tab re-prompts for SSO. Either make SSO **silent/transparent** for tabs after the first, or — preferably — **single sign-on once for the whole Developer Hub** and share the token across tabs (e.g. via shared MSAL cache, broadcast channel, or service-worker token broker). If sharing across tabs is not possible (e.g. cross-origin iframes with strict storage isolation), document the technical reason in [CHANGELOG.md](CHANGELOG.md) so we stop revisiting the question.

### Low-prio polish (open follow-ups)
- **Brand-color audit** — full `#005faa` audit across remaining status / link colors. `#555` `propLabel` already swapped to `tokens.colorNeutralForeground2`; rest still pending.
- **About content** — confirm About lists Lukasz + Alexander as authors, credits Michael Kovalsky for `semantic-link-labs`, and surfaces a single source-of-truth hub version (workload version constant lives in topbar — see WL-1 C8).
- **Workload icon verify** — after next `docker compose --profile prod build frontend`: trust dialog, Fabric favicon, and home-page tile render the new `developerHub.png` glyph.

---

## Remaining fixers (WS-E backlog)

25 fixer handlers shipped to date — 13 in v0.41, 6 in v0.50 (P1), 6 in v0.51 (P2). See [CHANGELOG.md](CHANGELOG.md) for the full list. Priority for the rest:

### IBCS workstream (P0 — Alexander's signature feature) — **WS-E-IBCS**

The IBCS feature set is the differentiator vs. other Power BI tooling. Treat as a single
cohesive batch — the Variance fixer depends on PY measures + Calendar + a Measure Table being
present, so they ship together. Also surface as a **one-click "Apply IBCS"** macro that runs
the full chain in order.

#### Spike result (May 5 2026) — backend approach locked

**Decision: dedicated `pbi-fixer-tom` sidecar container (Python 3.11 + `sempy-labs`), called from the FastAPI backend over the docker-compose network.**

Decisions (with Alexander):
1. **Approach** — sidecar with `sempy-labs`. Backend stays Python 3.13. Hard blocker: `semantic-link-labs/pyproject.toml` declares `requires-python = ">=3.10,<3.12"`, so adding it to the main backend would require a Python downgrade across the whole service. Sidecar isolates the .NET / pythonnet / mono dependency chain (~500 MB) from the lean FastAPI image.
2. **API shape** — one generic `POST /run-fixer` endpoint on the sidecar. Body: `{fixerId, workspaceId, datasetId, args}`. Backend dispatches without per-fixer routing changes.
3. **Auth** — backend forwards the existing OBO `PBI_API_TOKEN` (Power BI audience) to the sidecar as `X-PBI-Token`. Sidecar uses it for the XMLA write connection. Token never leaves the docker network.
4. **Code reuse** — fixers are **rewritten** as sidecar-native functions (NOT vendored from `PBI-Fixer/pbi_fixer/src/`). Original scripts stay as the reference implementation; sidecar versions are tighter, async-friendly, and only depend on `sempy_labs.tom`. This avoids dragging the entire `pbi_fixer` package + its CLI deps into the sidecar.
5. **First fixer to ship** — Add Measure Table (~150 LOC original, single calculated table `= {BLANK()}`). Smallest fixer that exercises the whole roundtrip, de-risks every later fixer.
6. **Calc groups deferred** — Time Intelligence + Units calc-group fixers are **out of v1**. IBCS Variance still works without them; revisit after v1 ships.

#### Architecture

```
┌──────────────────────┐    HTTP (internal)     ┌──────────────────────────┐
│ developerhub-backend │ ─────────────────────► │ pbi-fixer-tom            │
│ (Python 3.13)        │   POST /run-fixer       │ (Python 3.11 +           │
│                      │   X-PBI-Token: <obo>    │  sempy-labs + .NET 6)    │
│ /pbi-fixer/ibcs/...  │ ◄───────────────────── │                          │
└──────────────────────┘    {findings, log}      │ /run-fixer dispatcher    │
                                                  │  └─ measure_table()      │
                                                  │  └─ py_measures()        │
                                                  │  └─ calendar()           │
                                                  │  └─ ibcs_variance()      │
                                                  │  └─ ...                  │
                                                  └──────────────────────────┘
```

- **Service name:** `pbi-fixer-tom` (matches sibling `developerhub-backend`, `developerhub-frontend` naming).
- **Port:** internal-only on the compose network; not exposed to host.
- **Health:** `GET /health` returns `{ok: true, sempy_labs_version, dotnet_version}` so backend can verify wiring on startup.
- **Logging:** sidecar streams to its container log; backend tags every dispatch with `correlation_id` so traces line up.

#### Sidecar `Dockerfile` sketch

```dockerfile
FROM mcr.microsoft.com/dotnet/runtime:6.0 AS dotnet
FROM python:3.11-slim
COPY --from=dotnet /usr/share/dotnet /usr/share/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet PATH="$PATH:/usr/share/dotnet"
RUN apt-get update && apt-get install -y --no-install-recommends \
        libicu72 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen
COPY src/ ./src/
EXPOSE 5100
CMD ["python", "src/main.py"]
```

Sidecar deps (`pyproject.toml`):
```
fastapi, uvicorn, semantic-link-labs>=0.14.0, semantic-link-sempy>=0.14.0
```

#### Sidecar API contract

```http
POST /run-fixer
Headers: X-PBI-Token: <power-bi-audience-OBO>
         X-Correlation-Id: <uuid>
Body:    { "fixerId": "ibcs.measure_table",
           "workspaceId": "...", "datasetId": "...",
           "scanOnly": false,
           "args": { ... fixer-specific ... } }
Response 200: { "applied": true,
                "findings": [ {object: "MeasureTable", before: null, after: "created"} ],
                "log": ["..."] }
Response 4xx/5xx: { "error": "...", "log": [...] }
```

`fixerId` registry inside sidecar:
- `ibcs.measure_table` — Add empty measure-organization table
- `ibcs.py_measures` — Add `PY`, `Δ PY`, `Δ PY %`, `Max Green PY`, `Max Red AC` for each AC measure
- `ibcs.calendar` — Add CalcCalendar table + hierarchy + sort-by + auto-detected FK relationships
- `ibcs.variance` — IBCS Variance visual transform (uses `add_measure` if PY/Δ missing)
- `ibcs.fix_charts` — Unified chart cleanup (PBIR, but goes through sidecar for consistency)
- `ibcs.theme` — Push IBCS theme JSON

(Calc-group fixers `ibcs.ti_calcgroup`, `ibcs.units_calcgroup` reserved but **not implemented in v1**.)

#### Backend changes

- New module `Backend/src/services/agenthub/pbi_fixer_sidecar.py`:
  - `async def call_fixer(fixer_id, workspace_id, dataset_id, args, *, scan_only, pbi_token, correlation_id) -> SidecarResult`
  - Resolves sidecar URL from env (`PBI_FIXER_TOM_URL=http://pbi-fixer-tom:5100`).
  - 120s timeout (TOM model open + commit can take 30–60s on a cold semantic model).
  - Maps sidecar errors to HTTP 502 with the sidecar log embedded.
- New endpoint `POST /api/pbi-fixer/ibcs/{fixer}` in `agenthub_controller.py`:
  - Reuses existing `_mcp_tokens(request)` to get `PBI_API_TOKEN`.
  - Reuses existing `_rate_limit(user_id, "pbi_fixer_ibcs")` budget.
  - Forwards to sidecar.

#### Frontend changes

- New service `Frontend/src/components/PbiFixer/services/ibcsApi.ts`:
  - `runIbcsFixer(fixerId, {workspaceId, datasetId, scanOnly, ...args})`
  - One thin function reused by every IBCS fixer card.
- Existing `FixerPage.tsx`: append IBCS rows that call `runIbcsFixer`.

#### Ship order (each = independent commit + version bump)

| # | Fixer | Sidecar fn | Size | Notes |
|---|---|---|---|---|
| 1 | **Add Measure Table** | `measure_table()` | S | Proves whole sidecar roundtrip; ship + soak |
| 2 | **Add PY Measures** | `py_measures()` | S | Builds on #1; needed by IBCS Variance |
| 3 | **Add Calculated Calendar** | `calendar()` | M | Hierarchy + auto-FK detection |
| 4 | **Apply IBCS Theme** | `theme()` | S | Pure REST internally, but routed through sidecar for consistent observability |
| 5 | **Fix All Charts (IBCS pass)** | `fix_charts()` | M | PBIR mutation |
| 6 | **Fix IBCS Variance** | `variance()` | XL | 771-line original; port in 3 PRs: (a) **visual props + measure attach** (Y/Tooltip projections, Stacked→Clustered), (b) **error bars** (red `#FF0000` for negative Δ PY, green `#92D050` for positive — sourced from `_AC_COLOR`/`_PY_COLOR`/`_ERROR_RED`/`_ERROR_GREEN` constants in [_Fix_IBCSVariance.py](../../../../../../../PBI-Fixer/pbi_fixer/src/_Fix_IBCSVariance.py)), (c) **% data-label alignment** (Δ PY % overlay column right-aligned + IBCS-compliant overlap/labels/axes/sorting). All three sub-parts must ship for the visual to be IBCS-correct. |
| 7 | **One-click "Apply IBCS"** macro | backend orchestration | M | Sequential dispatch of #1 → #6, transactional rollback (capture & redeploy previous TMDL on any step failure) |

Each step ships behind a feature flag (`PBI_FIXER_IBCS_STEP_<N>=true` in `docker-compose.yaml`) so the chain can advance one fixer at a time without exposing half-built fixers in the UI.

#### Acceptance gates per step

For each fixer ship: (a) sidecar unit test against a recorded XMLA mock, (b) E2E test against the demo "Bad Report - Testing" model in a dev workspace, (c) frontend-visible scan-only run that previews findings before apply, (d) version bump + memory file update.

#### Risks / open questions
- **Sidecar startup time.** First TOM call after container boot warms up the .NET runtime; expect a 5–10s cold start. Mitigate by hitting `/health` from backend on startup so the .NET runtime is already JIT'd before the first user click.
- **PBI_API_TOKEN audience.** Verify the existing OBO grant chain produces a token usable for XMLA writes (some tenants restrict workload-OBO tokens to read-only scopes). If not, may need an additional consent prompt for `https://analysis.windows.net/powerbi/api/.default`. **First ship-blocker to validate.**
- **Container image size.** Estimated 800 MB–1 GB for sidecar (Python 3.11 + .NET 6 + sempy-labs + native libs). Acceptable for a dev workload but flag for production hardening.
- **IBCS Variance is 771 lines.** Port in 3 chunks (visual-property mutations / measure attachment / error-bar styling) to keep diffs reviewable.
- **Auto-FK detection** for Calendar relationships: replicate the Python `tom.model.Tables` walk via sempy-labs — find every column where `data_type==DateTime` and `is_key==False`, propose `add_relationship from=Table[Col] to=CalcCalendar[Date]`. Show the proposal list in a confirmation step before apply (no silent multi-relationship writes).

#### Deferred until after WS-E-IBCS lands
- Calc-group fixers (Time Intelligence + Units) — IBCS Variance still works without them.
- Real `tom.format_dax_expression` (DAX prettifier) — not on the IBCS path.
- Bulk undo / version snapshots — manual revert via Fabric portal is the v1 escape hatch.

#### Owns
- `pbi-fixer-tom/` (NEW directory at repo root in Fabric_Developer_Hub) — sidecar source + Dockerfile + tests
- `Fabric_Developer_Hub/Developer Hub/docker-compose.yaml` — add `pbi-fixer-tom` service
- `Backend/src/services/agenthub/pbi_fixer_sidecar.py` — **NEW** HTTP client to sidecar
- `Backend/src/api/agenthub_controller.py` — append `/pbi-fixer/ibcs/{fixer}` POST endpoint
- `Frontend/src/components/PbiFixer/services/ibcsApi.ts` — **NEW** (one fn per fixer + macro)
- `Frontend/src/components/PbiFixer/components/pages/FixerPage.tsx` — append IBCS fixer rows
- `Frontend/src/components/PbiFixer/utils/version.ts` — bumped per fixer ship

#### What was NOT done in this spike
No code shipped. This spike is purely the architectural decision + revised ship order + sidecar contract. Implementation kicks off with step 1 (Add Measure Table) when Alexander gives the go-ahead. The very first thing to validate is whether the existing OBO `PBI_API_TOKEN` is XMLA-write-capable — if not, the auth chain needs adjustment before any fixer code is meaningful.

---

#### Original IBCS scope reference (kept for context)

| Fixer | Python File | Lines | Notes |
|---|---|---|---|
| **Add Calculated Calendar Table** | `_Add_CalculatedTable_Calendar.py` | ~290 | DAX `CALENDARAUTO`, full date hierarchy + sort-by-column, **auto-detects fact-table date columns and creates Many→One relationships to `CalcCalendar[Date]`** |
| **Add Measure Table** | `_Add_CalculatedTable_MeasureTable.py` | ~150 | Empty calculated table for measure organization |
| **Add CalcGroup — Time Intelligence** | `_Add_CalcGroup_TimeIntelligence.py` | ~200 | YTD / MTD / QTD / PY / YoY calc-group items — **deferred (post-v1)** |
| **Add CalcGroup — Units** | `_Add_CalcGroup_Units.py` | ~150 | k / M / B unit scaling calc group — **deferred (post-v1)** |
| **Add PY Measures** | `_Add_PYMeasures.py` | ~150 | Generates `PY`, `Δ PY`, `Δ PY %`, `Max Green PY`, `Max Red AC` for every detected AC measure (prerequisite for IBCS Variance) |
| **Fix IBCS Variance** | `_Fix_IBCSVariance.py` | **771** | Transforms column charts → IBCS variance: ensures PY measures exist, adds them to visual, sets **error bars (red `#FF0000`)**, overlap, labels, AC/PY colors, axes, sorting. Largest single fixer in the suite |
| **Fix All Charts (IBCS pass)** | `_Fix_Charts.py` | ~200 | Unified IBCS-friendly cleanup across Bar/Column/Line: no gridlines, data labels on, clean axes |
| **Apply IBCS Theme** | (theme JSON in `_report_theme.py`) | ~100 | Push IBCS-compliant report theme |

### Report fixers (not yet ported)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Migrate Report-Level Measures | `report/_Fix_MigrateReportLevelMeasures.py` | ~150 | P2 |
| Upgrade PBIRLegacy → PBIR | `report/_Fix_UpgradeToPbir.py` | 100 | P2 (sempy-labs runtime) |
| Migrate Slicers → Slicerbar | `report/_Fix_MigrateSlicerToSlicerbar.py` | ~150 | P2 |
| Convert Column → Line | `_Fix_ColumnToLine.py` | 118 | P3 |

### Semantic Model fixers (not yet ported)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Trim Object Names | `_Fix_TrimObjectNames.py` | 71 | P2 (rename: touches every TMDL ref + PBIR binding) |
| Capitalize Object Names | `_Fix_CapitalizeObjectNames.py` | ~60 | P2 (rename: touches every TMDL ref + PBIR binding) |
| Month Column Format | `_Fix_MonthColumnFormat.py` | 38 | P3 |
| Sort Month Column | `_Fix_SortMonthColumn.py` | 61 | P3 |
| Flag Column Format | `_Fix_FlagColumnFormat.py` | 48 | P3 |
| IsAvailableInMDX (True) | `_Fix_IsAvailableInMdxTrue.py` | 49 | P3 |

### Top-level features (not yet ported)

| Feature | Python File | Lines | Priority |
|---|---|---|---|
| Prep for AI | `_Add_PrepForAI.py` | 501 | P2 |
| Cache Warming | `_Add_CacheWarming.py` | 269 | P3 |
| Incremental Refresh | `_Add_IncrementalRefresh.py` | 139 | P3 |

**Already shipped — do not re-port** (see CHANGELOG for details):
- v0.41 SM: `Fix_FloatingPointDataType`, `Fix_DoNotSummarize`, `Fix_DiscourageImplicitMeasures`, `Fix_IsAvailableInMdxFalse`, `Fix_MeasureFormat`, `Fix_PercentageFormat`, `Fix_WholeNumberFormat`, `Fix_HideForeignKeys`
- v0.41 Report: `Fix_PieChart`, `Fix_PageSize`, `Fix_HideVisualFilters`, `Fix_DisableShowItemsNoData`, `Fix_RemoveUnusedCustomVisuals`
- v0.50 SM: `Fix_AvoidAdding0`, `Add_LastRefreshTable`, `Add_MeasuresFromColumns`
- v0.50 Report: `Fix_BarChart`, `Fix_ColumnChart`, `Fix_VisualAlignment`
- v0.51 SM: `Fix_DateColumnFormat`, `Fix_DataCategory`, `Fix_MarkPrimaryKeys`, `Fix_MeasureDescriptions`, `Fix_UseDivideFunction`, `Fix_DefaultDataSourceVersion`

**Priority legend:**
- **P0** — IBCS batch (signature feature, ship as one workstream)
- **P1** — high user value, ship next batch
- **P2** — medium value, ship after P1 batch
- **P3** — niche / formatting polish, ship opportunistically

---

## WS-E-TEMPLATES — Inject community TMDL templates (P1 batch, NEW)

Each item below is a community-published TMDL fragment that should be injectable into a connected semantic model in one click. Mechanism mirrors `Add_LastRefreshTable` / `Add_CalculatedTable_Calendar` — backend handler in `pbi_fixer_handlers.py` receives the fixerId, fetches the model TMDL via the existing round-trip, parses the embedded TMDL fragment, prepends it under `definition/tables/` (or `definition/expressions/` for shared M expressions), checks for name collisions (skip-with-finding if the table / expression already exists), and writes back. Frontend exposes them as `backendFixer({...})` entries grouped under a new "Templates" tab in `FixerPage.tsx` (or under existing scope filters). Sources are pinned into `Backend/data/templates/` as `.tmdl` files so the build is reproducible — no live fetch from the community sites.

Grouped by purpose:

### Calendar templates
| Fixer | Source | Notes |
|---|---|---|
| `Add_CalcCalendar_Rich` | [community/Calc-Table-Calendar](https://community.fabric.microsoft.com/t5/TMDL-Gallery/Calc-Table-Calendar/td-p/4798025) | Calculated `CalcCalendar` table with hierarchies (`Date Hierarchy`, `Fiscal Date Hierarchy`, `Calendar Hierarchy`), display folders (`1. Favorites`, `2. Calendar Date`, `3. Fiscal Date`, `4. Flags`), 20+ flag/key columns, `MonthStartFiscalYear` parameterised (default 10). Supersedes the v0.50 `Add_LastRefreshTable`-era basic calendar — keep both, this one is the "rich" variant. |
| `Add_PqCalendar_LarsSchreiber` | [fabsnippets.com/snippet/13](https://fabsnippets.com/snippet/13) | Adds shared M expression `Kalenderfunktion` (Lars Schreiber's PQ date table function): full ISO weeks, fiscal year, relative units, "2 Go" flags, German + culture-aware day/month names. Injected as a shared expression so the user can call `fnKalender(2019, 1, "de-de", "Jul", "Mo")` from any new query. Implementation: append to `definition/expressions/Kalenderfunktion.tmdl`. |

### Measure-organisation templates
| Fixer | Source | Notes |
|---|---|---|
| `Add_MeasureTable_Empty` | [fabsnippets.com/snippet/9](https://fabsnippets.com/snippet/9) | Single empty `Measure` calc-table with one hidden `Value` column + `Sample Measure = NOW()`. Smallest possible measure-organisation seed. Skip-with-finding if any existing table contains "measure" (case-insensitive) — reuses the v0.50 `Add_LastRefreshTable` collision-detection helper. |
| `Add_MeasureTables_3WithIcons` | [community/3 Measure Tables With Icons](https://community.fabric.microsoft.com/t5/TMDL-Gallery/3-quot-Measure-Tables-quot-With-Icons/td-p/4774031) | Three calc-tables: `🎯Measures \| 1.📈KPIs`, `🎯Measures \| 2. #⃣ Variables`, `🎯Measures \| 3.📋Titles and Labels`. Visual grouping in the Fields pane via emoji prefix. Inject all three at once (atomic — either all created or none, to avoid leaving the user with a partial set). |

### Last-Refresh templates
| Fixer | Source | Notes |
|---|---|---|
| `Add_LastRefresh_LocalNow` | [fabsnippets.com/snippet/11](https://fabsnippets.com/snippet/11) | `Last Refresh` table with M source `DateTime.LocalNow()` + `Last Refresh Measure`. Equivalent to the existing v0.50 `Add_LastRefreshTable` but with the simpler M source from this snippet — surface as the "Local time" variant. |
| `Add_LastRefresh_EuropeMEZ` | [fabsnippets.com/snippet/12](https://fabsnippets.com/snippet/12) | `Last Refresh` table with M source converting `DateTimeZone.UtcNow()` to CET / CEST automatically (DST-aware). Adds the shared expression `UTC to CEST/CET` for reuse elsewhere. The "Europe time zone" variant — pick this when the user is on Fabric capacity in a non-CET region but wants to display CET timestamps. |

The three Last-Refresh fixers (existing `Add_LastRefreshTable` + the two new ones) are mutually exclusive — present as a radio-style picker in the UI when more than one would create a `Last Refresh` table.

### Model documentation templates
| Fixer | Source | Notes |
|---|---|---|
| `Add_ModelDocumentation` | [fabsnippets.com/snippet/14](https://fabsnippets.com/snippet/14) | Martyn Booth's documentation pack: hidden calc-tables `_Tables`, `_Columns`, `_DAX Measures`, `_Relationships` (sourced from `INFO.VIEW.TABLES()` / `INFO.VIEW.COLUMNS()` / etc.) plus a `Measure` table with ~25 SVG-based icon measures (True/False pills, cardinality pills, filter-direction pills) and dynamic title measures. ~600 lines TMDL — largest template by far. Acceptance: ship behind a confirmation dialog because the calc-tables refresh on every model refresh and add observable RAM (~5-20 MB on real models). Skip-with-finding if `_Tables` / `_Columns` / `_DAX Measures` / `_Relationships` already exist. |

### Implementation notes
- **One backend handler per fixer**, all reusing the same `inject_tmdl_fragment(model_tmdl, fragment_path, *, target="tables"|"expressions") -> AppliedResult` helper. Helper handles: collision check, lineageTag rewriting (regenerate UUIDs to avoid clashes), TMDL block insertion at the right offset, return of `{added: [tableName, ...], skipped: [{name, reason}]}`.
- **Pinned sources**: copy each TMDL into `Backend/data/templates/<fixerId>.tmdl` at WS-E-TEMPLATES kickoff so the build is reproducible and offline-capable. Add a one-line attribution header (`# Source: <url>  Author: <name>`) at the top of each file.
- **Frontend grouping**: add a `templateGroup` field to `backendFixer({...})` entries (`"calendar" | "measures" | "lastRefresh" | "documentation"`). `FixerPage.tsx` renders these under collapsible group headers above the existing `Fix_*` rows.
- **Ship order**: smallest first to validate the inject helper. (1) `Add_MeasureTable_Empty` → (2) `Add_LastRefresh_LocalNow` → (3) `Add_CalcCalendar_Rich` → (4) `Add_LastRefresh_EuropeMEZ` (adds shared expression injection) → (5) `Add_PqCalendar_LarsSchreiber` (shared expression only) → (6) `Add_MeasureTables_3WithIcons` (atomic multi-table) → (7) `Add_ModelDocumentation` (largest, behind confirmation).
- **Owns**: `Backend/data/templates/*.tmdl`, new helpers in `Backend/src/services/agenthub/pbi_fixer_handlers.py`, new entries in `Frontend/src/components/PbiFixer/fixers/index.ts`, optional grouping changes in `Frontend/src/components/PbiFixer/components/pages/FixerPage.tsx`.

---

## WS-MON — Workspace Monitoring deployment wizard (NEW, P1)

Multi-step orchestrated workflow that deploys the Microsoft Fabric Toolbox [Workspace Monitoring PBI Report](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/workspace-monitoring-dashboards/how-to/How_to_deploy_Workspace_Monitoring_PBI_Report.md) into a target workspace end-to-end. Not a single fixer — a four-step wizard with explicit user gates between each step so the user can verify intermediate state.

### Why this is its own workstream (not a fixer)
The existing `Fix_*` / `Add_*` fixers all act on an already-loaded semantic model. This workflow operates at the workspace level, touches three different Fabric APIs (Workspace Monitoring admin, Imports, Datasets), depends on an external file (`Fabric Workspace Monitoring Report.pbit`), and has a hard sequencing constraint: the eventhouse must be live before the report parameters can resolve. Cleaner as a wizard surface with named steps.

### Step-by-step contract

| # | Step | Backend call | UI | User gate |
|---|---|---|---|---|
| 1 | **Activate Workspace Monitoring** | `POST /v1/workspaces/{wsId}/monitoring` (Fabric REST) — enables the Monitoring Eventhouse on the target workspace | Picker for target workspace + "Activate" button. Polls until eventhouse status is `Active` (~1-2 min). Surfaces the resulting **Query URI** (eventhouse `queryServiceUri`) as a copy-able field. | "Continue to Step 2" button enabled only after eventhouse reports `Active` |
| 2 | **Modify the template** | `inject_pbit_parameters(pbit_bytes, {QueryURI, EventhouseName}) -> pbix_bytes` — pure local transform, no API call. Reads the pinned `.pbit` from `Backend/data/templates/Fabric_Workspace_Monitoring_Report.pbit`, opens it as a ZIP, rewrites the `DataModelSchema` Power Query M parameter defaults (`Query URI` = the URI from Step 1, `Eventhouse Name` = `"Monitoring Eventhouse"`), repacks. | Read-only preview of the resolved parameter values + a "Preview the embedded M expression" disclosure for transparency. | "Continue to Step 3" button |
| 3 | **Publish the report** | `POST /v1.0/myorg/groups/{wsId}/imports?datasetDisplayName=Fabric%20Workspace%20Monitoring%20Report&nameConflict=CreateOrOverwrite` (Power BI REST Imports API) — multipart upload of the modified bytes. Poll `GET .../imports/{importId}` until `importState=Succeeded` (or `Failed`, surface the error). | Progress bar + the resulting `datasetId` and `reportId` once the import succeeds. | "Continue to Step 4" button |
| 4 | **Take ownership + refresh** | (a) `POST /v1.0/myorg/groups/{wsId}/datasets/{datasetId}/Default.TakeOver` (otherwise the OAuth user owns nothing and the credential edit fails); (b) `PATCH /v1.0/myorg/gateways/{gatewayId}/datasources/{datasourceId}` to set OAuth2 credentials inherited from the signed-in user's token; (c) `POST /v1.0/myorg/groups/{wsId}/datasets/{datasetId}/refreshes`. Poll the refresh history until `status=Completed` or `Failed`. | Three sub-progress chips (Take Ownership / Set Credentials / Refresh). | "Open report in Fabric" link button on success. |

Each step has its own backend endpoint so the user can re-run any step independently (e.g. activation succeeded but parameter modification needs re-running with a different eventhouse name). Wizard remembers the resolved `(workspaceId, queryUri, datasetId, reportId)` in `sessionStorage["pbiFixer.monitoring.v1"]`.

### Backend endpoints

```
POST /api/pbi-fixer/monitoring/activate           body: {workspaceId}                  → {queryUri, status}
POST /api/pbi-fixer/monitoring/modify-template    body: {queryUri, eventhouseName}     → {pbixBlobId}     (server-side blob, 5 min TTL)
POST /api/pbi-fixer/monitoring/publish            body: {workspaceId, pbixBlobId}      → {datasetId, reportId, importId}
POST /api/pbi-fixer/monitoring/finalize           body: {workspaceId, datasetId}       → {refreshId, status}
GET  /api/pbi-fixer/monitoring/status             query: {workspaceId, importId?, refreshId?}  → polling status
```

### Pinned assets
- `Backend/data/templates/Fabric_Workspace_Monitoring_Report.pbit` — committed binary, pinned to the toolbox commit hash recorded in `Backend/data/templates/Fabric_Workspace_Monitoring_Report.SOURCE.md`. Refresh the binary manually when Microsoft updates the upstream `.pbit`; do **not** fetch it live from GitHub at runtime (no network during fixer execution + reproducible builds).

### Owns
- `Backend/data/templates/Fabric_Workspace_Monitoring_Report.pbit` (NEW binary asset)
- `Backend/src/services/agenthub/workspace_monitoring.py` (NEW — pbit ZIP rewrite + 4 step orchestrators)
- `Backend/src/api/agenthub_controller.py` — append the 5 endpoints above
- `Frontend/src/components/PbiFixer/components/pages/MonitoringWizardPage.tsx` (NEW)
- `Frontend/src/components/PbiFixer/services/monitoringApi.ts` (NEW)
- New nav entry under `pbiFixerNav` group `modelTools` (key `monitoring`, label "Workspace Monitoring", glyph 📡)

### Risks / open questions
- **Workspace Monitoring requires Fabric capacity** (F SKU, not Power BI Premium per user). Surface a precondition check in Step 1 — call `GET /v1/workspaces/{wsId}` and bail with an actionable error if `capacityAssignmentProgress` is not `Completed` or the capacity SKU is not F-tier.
- **Eventhouse activation latency** — Microsoft docs cite 1-2 minutes typical, up to 5. Poll with exponential backoff capped at 5 min total; surface a "still working…" message after 90s so the user knows we haven't hung.
- **Take-Over permission** — the signed-in user must be a workspace Admin or Member. If they are only Contributor, the TakeOver call returns 401. Pre-flight by calling `GET /v1.0/myorg/groups/{wsId}/users` and matching the user's principal.
- **PBIT vs PBIR** — the toolbox ships a `.pbit` (legacy template format). Once Microsoft publishes a `.pbip` / PBIR-native version we should switch and use the existing PBIR write-back path instead of the ZIP rewrite. Track upstream.
- **Daily refresh schedule** — the deployment doc recommends a daily refresh because the Calendar table needs to roll forward. Step 4 should also `PATCH .../refreshSchedule` to set `enabled: true, days: ["Monday"..."Sunday"], times: ["06:00"]`. Make this configurable in Step 4 (default: daily at 06:00 capacity time).

### Ship order
1. **Step 2 alone** (offline pbit rewrite + unit test against a recorded pbit fixture). Smallest, no Fabric calls. De-risks the ZIP roundtrip.
2. **Step 1 alone** behind a feature flag — manual workspace activation + URI display. Verify against demo workspace.
3. **Step 3** (publish via Imports API). Validate import succeeds with the modified pbit bytes.
4. **Step 4** (take-over + credentials + refresh). Most complex auth chain; ship last.
5. **Wizard UI** stitching all four together with the user gates.

### Non-goals (v1)
- Updating an already-deployed report (re-run the wizard creates a new one with `nameConflict=CreateOrOverwrite`; we don't try to surgically patch the existing dataset's parameters).
- Cross-workspace reporting (each Workspace Monitoring deployment is per-workspace by design — the eventhouse only sees its own workspace's logs).
- Custom theme / branding overlays on the published report.

---

## WS-FUAM — Fabric Unified Admin Monitoring deployment wizard (NEW, P1)

End-to-end orchestrator for deploying [FUAM](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring) (Fabric Unified Admin Monitoring, currently release `2026.2.1`). FUAM is a multi-item solution accelerator (pipelines, notebooks, lakehouse, two semantic models, one core report) and the upstream deploy guide has 8 manual steps. WS-FUAM compresses those into a guided 7-step wizard that calls Fabric REST + the upstream `Deploy_FUAM.ipynb` notebook directly, surfaces every parameter the user has to provide up-front, and remembers state between steps.

### Why a dedicated workstream
Bigger and more brittle than WS-MON:
- Requires a Service Principal (client secret) created out-of-band in Entra ID.
- Requires F-capacity (P-capacity acceptable, PPU and Pro NOT supported).
- Requires the user to be a permanent **Fabric Administrator** at tenant level (or to provide an Azure Key Vault that holds an SPN with admin rights).
- Requires the **Fabric Capacity Metrics App** (compatible versions: v53, v47, v44 or earlier) installed in a sibling workspace on F/P capacity with XMLA Read enabled.
- Requires two tenant-admin settings to be enabled for a security group containing the SPN: "Service Principals can use Fabric APIs" and "Service Principals can access read-only admin APIs".
- The deploy itself runs the upstream `Deploy_FUAM.ipynb` which downloads the entire `src/` zip from GitHub at runtime — known to fail behind Private Link, so we ship a fallback path that uses a pinned local copy.

### Wizard step-by-step contract

| # | Step | Backend call | UI | User gate |
|---|---|---|---|---|
| 0 | **Pre-flight checks** | Local + 3 read-only Fabric REST calls. (a) Verify signed-in user has `Fabric Administrator` Entra role via `GET https://graph.microsoft.com/v1.0/me/memberOf` filtered for the Fabric Admin role definition; (b) verify the user can create workspaces (`GET /v1/workspaces` returns 200, no permission denied); (c) verify the target capacity is F or P SKU (`GET /v1/capacities` filtered by id). | Read-only summary card with green checks / red Xs and an actionable error message per failed check ("You are not a Fabric Administrator — sign in with a different account or provide a Key Vault SPN in Step 4"). | "All checks passed" required to continue. Allow override-with-warning if only the optional KV path was missing. |
| 1 | **Create the FUAM workspace** | `POST /v1/workspaces` with `displayName` (default `"FUAM"`, editable), `capacityId` (picker bound to the user's available F/P capacities). Optionally upload the FUAM workspace icon from the pinned asset (`Backend/data/templates/fuam_workspace_icon.png`) via `POST /v1/workspaces/{wsId}` PATCH. | Workspace name field + capacity picker. After creation, surface the new `workspaceId`. | Workspace must exist before continuing. |
| 2 | **Capture Service Principal credentials** | None at this step — purely a form. Collect `tenantId`, `clientId`, `clientSecret` (password-masked input) from the user. We do NOT create the SPN — the upstream guide is explicit that the SPN must be created manually in Entra ID without API permissions and added to the right security group, both of which require Entra Admin consent. We surface the [exact required tenant settings](https://learn.microsoft.com/en-us/fabric/admin/enable-service-principal-admin-apis) as a checklist with deep links. | Three text inputs + checklist with inline links to the admin portal pages. **Optional toggle**: "Use Azure Key Vault" — collects KV name + 3 secret names (`tenantId`, `clientId`, `secret`) instead, which is the recommended path per the FUAM Authorization doc. | "I confirm the SPN is created and the two `Service Principals can…` admin settings are enabled for a group containing this SPN" — explicit checkbox. |
| 3 | **Capacity Metrics App discovery** | `GET /v1/workspaces?$filter=...` to find a workspace whose name contains `Capacity Metrics`. If none found, surface the install link ([Capacity Metrics App](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)) and explicit instructions: install app → rename workspace to `FUAM Capacity Metrics` → attach to F/P capacity → return here. If found, also `GET /v1/workspaces/{metricsWsId}/items?type=SemanticModel` and surface the semantic model name (default Microsoft ships is `Fabric Capacity Metrics`). Verify the metrics app version by reading the semantic model's annotation `app_version` — warn if outside `{v44, v47, v53}`. | Auto-populated workspace name + semantic model name fields, both editable. Version compatibility chip (green for known-good, amber otherwise with link to compatibility matrix). | "Continue" once both fields resolve. |
| 4 | **Run Deploy_FUAM.ipynb in the FUAM workspace** | (a) Upload the pinned notebook `Backend/data/templates/Deploy_FUAM.ipynb` (synced from the [latest upstream commit](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/scripts/Deploy_FUAM.ipynb)) into the FUAM workspace via `POST /v1/workspaces/{wsId}/notebooks` (Items API with `definition.parts[]` containing the base64'd `.ipynb` payload). (b) Trigger an on-demand run via `POST /v1/workspaces/{wsId}/items/{notebookId}/jobs/instances?jobType=RunNotebook`. (c) Poll `GET /v1/workspaces/{wsId}/items/{notebookId}/jobs/instances/{jobInstanceId}` until status `Completed` or `Failed`. The notebook itself creates the lakehouse, the two semantic models, the core report, all sub-pipelines, and the two cloud connections (`fuam pbi-service-api admin`, `fuam fabric-service-api admin`) without credentials. | Live job-status card with a streaming "Deployment notebook output" pane (read from `GET /v1/workspaces/{wsId}/items/{notebookId}/jobs/instances/{jobInstanceId}/livyEndpoint` snapshots) so the user sees the same output the upstream guide screenshots show. Expected duration: 10–20 min on F2, faster on bigger SKUs. | Job must reach `Completed`. On `Failed`, surface the cell that failed + the recovery procedure for the Private Link / `src.zip` fallback. |
| 5 | **Bind credentials to the two API connections** | (a) `GET /v1/workspaces/{wsId}/connections` to find the two `Web v2` connections created by the notebook in Step 4. (b) For each, `PATCH .../connections/{connectionId}` with `credentialDetails: {credentialType: "ServicePrincipal", servicePrincipalCredentials: {tenantId, clientId, clientSecret}}` from Step 2 (or KV references). (c) Verify by hitting a no-op REST endpoint via the connection (Fabric Connection Test API). | Per-connection status row (Connection Name / Type / Base URL / Bound ✅ or ❌ with retry button). | Both connections must be bound. |
| 6 | **Run `Load_FUAM_Data_E2E` for initial load** | (a) Find the pipeline by name in the FUAM workspace via `GET /v1/workspaces/{wsId}/items?type=DataPipeline`. (b) `POST /v1/workspaces/{wsId}/items/{pipelineId}/jobs/instances?jobType=Pipeline` with parameters: `metric_workspace=<from Step 3>`, `metric_dataset=<from Step 3>`, `metric_days_in_scope=14` (max for initial load), `activity_days_in_scope=28` (initial recommended), `display_data=true` (for the first run only — easier debugging), `optional_keyvault_*` from Step 2 (only if KV toggle was used), other params at defaults. (c) Poll until terminal state. Initial load typically takes 30–90 min depending on tenant size. | Parameter-summary table (read-only, all 14+ params with their resolved values), then a live job-status card. Expandable per-sub-pipeline drill-down ("Loading Tenant Settings…", "Loading Activities…", "Loading Capacity Metrics…") if we can extract that detail from the Livy log stream. | Job must reach `Completed`. |
| 7 | **Refresh the two semantic models, schedule daily run** | (a) `POST /v1.0/myorg/groups/{wsId}/datasets/{datasetId}/refreshes` for `FUAM_Core_SM` and `FUAM_Item_SM` (resolve IDs via the Items API). (b) Poll both refreshes until `Completed`. (c) `PATCH /v1/workspaces/{wsId}/items/{pipelineId}/schedules` to add a daily schedule (default 06:00 capacity-local). (d) Update the pipeline's parameter defaults via `POST .../jobs/instances` overrides — change `metric_days_in_scope` and `activity_days_in_scope` from initial values to `2` for incremental loads. | Two refresh-status cards + schedule card with editable time. Final "Open FUAM_Core_Report" deep link button. | Done. |

### Backend endpoints

```
GET  /api/pbi-fixer/fuam/preflight                                         → {checks: [{name, ok, message}]}
POST /api/pbi-fixer/fuam/workspace                body: {name, capacityId} → {workspaceId, capacityName}
POST /api/pbi-fixer/fuam/credentials              body: {strategy: "spn"|"keyvault", spn?: {tenantId, clientId, clientSecret}, kv?: {vaultName, tenantIdSecretName, clientIdSecretName, secretSecretName}}  → {credentialsBlobId}    (server-side blob, encrypted-at-rest, 30-min TTL)
POST /api/pbi-fixer/fuam/discover-metrics-app     body: {}                 → {metricsWorkspaceName, metricsDatasetName, version, versionStatus}
POST /api/pbi-fixer/fuam/deploy-notebook          body: {workspaceId}      → {notebookId, jobInstanceId}
POST /api/pbi-fixer/fuam/bind-connections         body: {workspaceId, credentialsBlobId}  → {bound: [{name, ok}]}
POST /api/pbi-fixer/fuam/initial-load             body: {workspaceId, metricsWorkspaceName, metricsDatasetName, credentialsBlobId, params: {...}}  → {pipelineId, jobInstanceId}
POST /api/pbi-fixer/fuam/finalize                 body: {workspaceId, scheduleTime}  → {refreshIds: [...], scheduleId}
GET  /api/pbi-fixer/fuam/job-status               query: {workspaceId, itemId, jobInstanceId} → {status, livyTail?}
```

### Pinned assets

- `Backend/data/templates/Deploy_FUAM.ipynb` — pinned to release `2026.2.1`. The notebook's hard-coded GitHub URL for the `src.zip` download must be patched at upload time so it points to a version we have validated. Source URL recorded in `Backend/data/templates/Deploy_FUAM.SOURCE.md`.
- `Backend/data/templates/fuam_src_2026.2.1.zip` — fallback bundle for Private Link / air-gapped deployments. Contains the `src/` folder + `config/deployment_order.json` + `config/item_config.yaml` + `data/table_definitions.snappy.parquet`. When pre-flight detects the user is on Private Link (heuristic: outbound to `raw.githubusercontent.com` blocked from a probe), we upload this zip into `FUAM_Lakehouse/Files/` first and patch the notebook's `src_file_path` cell to point at the local copy before triggering the run. Documented in the [Known Errors / Private Link](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_deploy_FUAM.md#known-errors) section of the upstream guide.
- `Backend/data/templates/fuam_workspace_icon.png` — optional FUAM workspace icon, applied in Step 1.

### Secret handling

User-provided SPN secrets MUST NOT touch disk in plain text. Storage strategy:
1. Encrypt with the backend's existing per-tenant data-encryption key (`AGENTHUB_DATA_KEY`, already used for token caching) before writing to the 30-min TTL blob.
2. Store only the encrypted blob + a HMAC fingerprint, never the plaintext, in `Backend/.data/Org.AgentHub/{tenantId}/_fuam-staging/{credentialsBlobId}.enc`.
3. After Step 5 (connections bound) succeeds, delete the staging blob immediately — credentials live only in Fabric's connection store from then on.
4. KV strategy never stores the secret server-side; only the KV reference set goes into the encrypted blob.

### Owns

- `Backend/data/templates/Deploy_FUAM.ipynb`, `Backend/data/templates/fuam_src_2026.2.1.zip`, `Backend/data/templates/fuam_workspace_icon.png` (NEW pinned binaries)
- `Backend/data/templates/Deploy_FUAM.SOURCE.md` (NEW — version pin + checksum)
- `Backend/src/services/agenthub/fuam_deployment.py` (NEW — orchestrator + 9 step handlers + secret encryption helpers + Private-Link probe)
- `Backend/src/api/agenthub_controller.py` — append the 9 endpoints above
- `Frontend/src/components/PbiFixer/components/pages/FuamWizardPage.tsx` (NEW — 8-step wizard with React Router-style step routing so users can deep-link or refresh and stay on the same step)
- `Frontend/src/components/PbiFixer/services/fuamApi.ts` (NEW)
- `Frontend/src/components/PbiFixer/components/pages/FuamWizardPage.fuamSteps.ts` (NEW — extracted step definitions for testability)
- New nav entry under `pbiFixerNav` group `modelTools` (key `fuam`, label "FUAM Deployment", glyph 🛡️)

### Risks / open questions

- **Notebook orchestration via Items API.** The `POST /v1/workspaces/{wsId}/items/{notebookId}/jobs/instances` endpoint exists but is documented as preview. Test against the demo workspace before relying on it. Fallback: surface a "Click here to open the notebook in Fabric, then click Run All" deep link if the API call returns 501. The wizard would then poll the run via `GET .../jobs/instances` once the user reports the run started — same polling, manual trigger.
- **Service Principal admin settings** can only be enabled by a Fabric Administrator via the admin portal — we cannot automate this. Surface the deep link `https://app.powerbi.com/admin-portal/tenantSettings` + the exact setting names in Step 2's checklist and require explicit confirmation.
- **Capacity Metrics App version drift.** The compatibility matrix changes when Microsoft ships breaking changes to the Metrics App. Pin the matrix in `Backend/data/templates/fuam_metrics_compat.json` and surface "outdated app, may not work" warnings rather than hard-blocking.
- **Initial load duration.** 30–90 min is a long time to keep a wizard alive. The status pages must be browser-refresh-resistant (load state from `sessionStorage["pbiFixer.fuam.v1"]` + the `fuam/job-status` endpoint, not from in-memory React state).
- **Anonymisation defaults.** The pipeline has `activity_anonymize_tables` + `activity_anonymize_after_days` parameters. EU-customer-friendly defaults: enabled, anonymise after 90 days. Surface as Step 6 advanced options with a "Recommended for GDPR" preset.
- **Pipeline schedule time zone.** Fabric pipeline schedules use the user's profile time zone, not the capacity time zone. Document this explicitly in Step 7's UI.

### Ship order

1. **Step 0 (pre-flight) only** — read-only, no mutations, biggest UX value relative to effort. Ship behind a feature flag and let users run the checks against their tenant before we build any deployment logic.
2. **Step 1 (workspace creation)** — already a single REST call, validate the capacity-picker UX.
3. **Step 4 (notebook upload + run)** — biggest technical risk. Validate against the demo tenant. If the Items API jobs path is unreliable, fall back to the manual-trigger flow before building the rest.
4. **Step 5 (bind connections)** — depends on Step 4 having created the connections; validate end-to-end.
5. **Step 6 (initial load)** — long-running, build the resilient status polling here.
6. **Steps 2 + 3 + 7 + UI wizard** — wire up the form steps and the final refresh + schedule once the heavy lifting works.

### Non-goals (v1)

- **Updating an already-deployed FUAM** — the upstream repo has a separate [How_to_update_FUAM](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_update_FUAM.md) procedure. Cover in WS-FUAM v2 (just re-run the Deploy notebook with overwrite-by-name, but the SM versions need handling).
- **Multi-tenant FUAM deployments.** One FUAM per tenant per session. To deploy to a different tenant, the user signs out and back in.
- **Programmatic SPN creation in Entra.** Hard policy boundary — users with rights to create SPNs can do it themselves; we won't proxy that.
- **Custom-rule Fabric Capacity Metrics extraction.** The Metrics App version compatibility is what it is; we pin to known-good versions and warn otherwise.

---

## WS-FCA — Fabric Cost Analysis deployment wizard (NEW, P1)

End-to-end orchestrator for deploying [FCA](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-cost-analysis) (Fabric Cost Analysis), the FinOps-focused sibling of FUAM. Maps the upstream [Deploy.md](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-cost-analysis/Deploy.md) 5-section guide (+ optional Reservation + Quota modules) into an explicit step-by-step wizard. Same architectural pattern as WS-FUAM (notebook-driven deployment + cloud connection binding + pipeline orchestration), but with two complications FUAM doesn't have: (a) the cost data lives in Azure Storage outside Fabric and must be ingested via a OneLake shortcut, (b) the Cost Management Export must be created in the Azure portal first.

### Why a dedicated workstream
Different audience and different prerequisite chain than FUAM:
- Requires **Azure subscription RBAC roles** (Cost Management Contributor for the export, Storage Blob Data Contributor for the shortcut, Reservation Reader for the optional reservation module, Contributor or higher for the Quota module).
- Requires an **Azure Data Lake Storage Gen2** account (or the user's existing FinOps Hubs storage) — outside Fabric.
- Requires the user to **create a Cost Management FOCUS export** in the Azure portal — there is no usable Cost Management API for creating exports interactively (the docs even cite the portal walkthrough as canonical).
- Optional second pipeline activity for Reservations (CSV export) and a third for Quotas (Azure Management REST API via cloud connection).
- Optional Data Agent on top of the FCA semantic model (separate notebook, separate prerequisites — Fabric Copilot capacity etc.).

### Wizard step-by-step contract

| # | Step | Backend call | UI | User gate |
|---|---|---|---|---|
| 0 | **Pre-flight checks** | (a) Verify the user can pick at least one Azure subscription (Microsoft Graph + ARM `GET https://management.azure.com/subscriptions?api-version=2020-01-01`); (b) verify the chosen subscription's signed-in identity has `Cost Management Contributor` and `Storage Blob Data Contributor` (call ARM `GET .../providers/Microsoft.Authorization/roleAssignments?$filter=...`); (c) verify a Fabric workspace can be created (Trial/F/P capacity available). | Subscription picker + capacity picker + green/red checks per role. Optional toggles "I plan to deploy Reservations" and "I plan to deploy Quotas" — flips on the Reservation Reader / Subscription Contributor checks accordingly. **Toggle "I already have FinOps Hubs"** — if on, the FOCUS export setup step is skipped and the user provides the FinOps Hubs storage URL + container instead. | All required checks must pass for the selected toggles. |
| 1 | **Create or select the Azure storage account** | (a) `GET https://management.azure.com/subscriptions/{subId}/providers/Microsoft.Storage/storageAccounts?api-version=2023-05-01` — list ADLS Gen2 storage accounts in the subscription. (b) Filter to `kind=StorageV2` AND `properties.isHnsEnabled=true` (HNS is required for shortcuts). (c) Optional create-new path: `PUT .../resourceGroups/{rg}/providers/Microsoft.Storage/storageAccounts/{name}` with `kind=StorageV2`, `isHnsEnabled=true`, `sku.name=Standard_LRS`, location matching the user's region. (d) Verify a `fca` container exists or create it via the data plane (`PUT https://{account}.blob.core.windows.net/fca?restype=container`). | Picker showing existing HNS-enabled accounts + "Create new" button with name + region + RG fields. Surface the resulting `dfsEndpoint` (`https://{account}.dfs.core.windows.net`) since we'll need it in Step 4. | Storage account exists with `fca` container. |
| 2 | **Create the FOCUS Cost Management export** | (a) Set scope (subscription, resource group, billing account, or management group — picker). (b) `PUT https://management.azure.com/{scope}/providers/Microsoft.CostManagement/exports/{exportName}?api-version=2023-08-01` with body specifying template `Cost and usage (FOCUS)`, schedule `Daily`, format `Parquet`, compression `Snappy`, overwrite=true, destination = the storage account from Step 1, container=`fca`, root folder=`fca`, prefix=`fca`. (c) Trigger an immediate run with `POST .../exports/{exportName}/run`. (d) Poll `GET .../exports/{exportName}/runHistory` until `status=Completed`. (e) **Optional**: offer a "Backfill 12 months" toggle — issues 12 separate one-month-window export runs in series so the FCA report has historical data on day one (per the upstream "Historical data in one-month chunks" note). Skip Step 2 entirely if the user toggled "I already have FinOps Hubs" in Step 0. | Scope picker (subscription / RG / billing-account / MG with discovery via ARM). Backfill toggle. Live progress for the initial run; per-month progress chips for the backfill. Surface a copy-able link to the export in the Azure portal in case the user wants to verify the storage layout manually. | First export run must complete successfully (otherwise the Lakehouse shortcut would point at empty folders). |
| 3 | **Create the FCA workspace** | `POST /v1/workspaces` with `displayName` (default `"FCA"`, editable), `capacityId` (Trial/F/P picker). Optionally upload the FCA workspace icon (`Backend/data/templates/fca_workspace_icon.png`). Optionally enable the workspace's "high-concurrency session" sharing for pipelines per the upstream guide's recommendation (saves Spark vCores during parallel notebook execution) — single PATCH against the workspace settings. | Workspace name + capacity picker + "Enable high-concurrency for pipelines" toggle (default ON, with explainer tooltip). | Workspace must exist. |
| 4 | **Run `00_Deploy_FCA.ipynb` in the FCA workspace** | (a) Upload the pinned notebook `Backend/data/templates/00_Deploy_FCA.ipynb` (synced from the [latest upstream commit](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-cost-analysis/script/00_Deploy_FCA.ipynb)) into the FCA workspace via the Items API (same plumbing as WS-FUAM Step 4). (b) Trigger an on-demand run via `POST /v1/workspaces/{wsId}/items/{notebookId}/jobs/instances?jobType=RunNotebook`. (c) Poll until `Completed`. The notebook creates the FCA Lakehouse, the `Load FCA E2E Data` pipeline, the `FCA_Core_SM` semantic model, the `FCA_Core_Report`, and the `fca azure management` cloud connection (used later for Quotas). | Live job-status card with streaming output. Watch out for the documented "Missing permissions on the existing connection" warning — surface as a yellow info chip with the upstream guidance ("contact the admin to grant permissions, then re-run this step"). Expected duration: 5–15 min on F2. | Job must reach `Completed`. Re-runnable per the upstream's update-via-rerun mechanism. |
| 5 | **Create the OneLake shortcut to the FOCUS export** | (a) `GET /v1/workspaces/{wsId}/items?type=Lakehouse` to find the `FCA_Lakehouse` (or whatever the upstream notebook named it). (b) `POST /v1/workspaces/{wsId}/items/{lakehouseId}/shortcuts` with `path=Files`, `name=focuscost`, `target.adlsGen2={location: "<dfsEndpoint from Step 1>", subpath: "/fca/fca", connectionId: "<new or reused>"}`. (c) If a connection doesn't already exist for this storage account, create it first via `POST /v1/connections` with `connectionDetails.type=AzureDataLakeStorage` + `credentialDetails.credentialType=OAuth2` (signed-in user) — same pattern documented in the upstream "Authentication kind = Organizational account" instruction. (d) Verify by listing the shortcut contents (`GET .../shortcuts/focuscost/contents` or equivalent) and surfacing the first parquet file path so the user can see data flowed through. | Shortcut-status card with "Connection: <name>" + "Target: <subpath>" + "First file: <name>" rows. Inline button "Test shortcut" runs the verify call again. | Shortcut must exist + return non-empty contents. |
| 6 | **(Optional) Reservation module** | Skip if Step 0's toggle was off. (a) Ask the user to create a second Cost Management export (template `All reservation data`, format `csv`, container `fca`, directory `reservation`) — surface the same step-by-step but for CSV/reservation. We CAN automate this via the same `PUT exports` call as Step 2 with a different body. (b) Create two more shortcuts (`reservation-details`, `reservation-transactions`) under `Files` pointing at the same storage. (c) Patch the `Load FCA E2E Data` pipeline to enable the `Load Reservations` activity — Items API `POST .../items/{pipelineId}/updateDefinition` with the modified pipeline JSON (set `activityState=Active` on the deactivated activity). | Auto-deploy toggle ("Create reservation export now" vs "I'll create it manually") + the two shortcut-status cards + the pipeline-activity flip confirmation. | Reservation export run completes + both shortcuts populated + pipeline activity activated. |
| 7 | **(Optional) Quota module** | Skip if Step 0's toggle was off. (a) Bind credentials to the `fca azure management` cloud connection that the Step 4 notebook created — same pattern as WS-FUAM Step 5: `PATCH .../connections/{connectionId}` with OAuth2 (signed-in user) or SPN credentials. (b) Patch the `Load FCA E2E Data` pipeline to enable the `Load Quotas` activity. (c) Verify the connection by hitting `GET https://management.azure.com/subscriptions?api-version=2020-01-01` through the connection. | Connection-status card + the pipeline-activity flip. SPN vs OAuth toggle (default OAuth — simpler). | Connection bound + 200 from the verify call + activity enabled. |
| 8 | **Run `Load FCA E2E Data` for initial load** | (a) `GET /v1/workspaces/{wsId}/items?type=DataPipeline` to find the pipeline by name. (b) `POST /v1/workspaces/{wsId}/items/{pipelineId}/jobs/instances?jobType=Pipeline` with parameters `FromMonth=-12, ToMonth=0` for backfilled deployments or `FromMonth=-3, ToMonth=0` for fresh deployments (configurable in the UI). (c) Poll until terminal state. Expected duration: 10–30 min depending on the export size. | Parameter card (FromMonth / ToMonth as numeric steppers with quick-presets "Last 12 months", "Last 3 months", "Current month only") + live job-status card. | Job must reach `Completed`. |
| 9 | **Refresh semantic model + schedule daily run** | (a) Find `FCA_Core_SM` via Items API. (b) Trigger SQL-endpoint metadata refresh on the FCA Lakehouse first (per the upstream's "Refresh the SQL Endpoint of the Lakehouse and update the semantic model in case of errors" note) — Lakehouse `POST .../sqlEndpoint/refreshMetadata` if available, otherwise surface a manual deep link. (c) `POST /v1.0/myorg/groups/{wsId}/datasets/{datasetId}/refreshes` for `FCA_Core_SM`. Poll until `Completed`. (d) `PATCH .../items/{pipelineId}/schedules` to add a daily schedule (default 04:00 capacity-local). (e) Update pipeline parameter defaults to `FromMonth=-1, ToMonth=0` for incremental loads (per the upstream recommendation that handles last-week-of-prior-month FOCUS revisions). | SQL-endpoint refresh chip + SM refresh chip + schedule card with editable time. Final "Open FCA_Core_Report" deep link. | Done. |
| 10 | **(Optional) Deploy the Data Agent** | Skip unless explicitly enabled. (a) Pre-flight the Fabric Data Agent prerequisites — Copilot capacity, AI Studio enabled, etc. — call `GET /v1/workspaces/{wsId}` and surface the missing capability chips. (b) Upload `Backend/data/templates/02_Create_DataAgent.ipynb` (pinned). (c) Run via Items API. (d) Poll until `Completed`. (e) Surface deep link to the resulting `FCA_Agent` item. | Pre-flight chips + run-status card + deep link. | Agent reachable, otherwise surface the prereq error. |

### Backend endpoints

```
GET  /api/pbi-fixer/fca/preflight                              query: {subscriptionId, deployReservations, deployQuotas, useFinOpsHubs}
POST /api/pbi-fixer/fca/storage                                body: {subscriptionId, resourceGroup, accountName, region, createNew}    → {dfsEndpoint, containerName}
POST /api/pbi-fixer/fca/cost-export                            body: {scope, dfsEndpoint, backfillMonths}    → {exportName, runId}
POST /api/pbi-fixer/fca/workspace                              body: {name, capacityId, enableHighConcurrency}   → {workspaceId}
POST /api/pbi-fixer/fca/deploy-notebook                        body: {workspaceId}                                → {notebookId, jobInstanceId}
POST /api/pbi-fixer/fca/shortcut-focus                         body: {workspaceId, dfsEndpoint, subpath}          → {shortcutId, firstFile}
POST /api/pbi-fixer/fca/reservations                           body: {scope, dfsEndpoint, workspaceId}            → {exportName, shortcutIds, pipelineActivityState}
POST /api/pbi-fixer/fca/quotas                                 body: {workspaceId, credentials}                   → {connectionId, pipelineActivityState}
POST /api/pbi-fixer/fca/initial-load                           body: {workspaceId, fromMonth, toMonth}            → {pipelineId, jobInstanceId}
POST /api/pbi-fixer/fca/finalize                               body: {workspaceId, scheduleTime, incrementalParams}  → {refreshId, scheduleId}
POST /api/pbi-fixer/fca/data-agent                             body: {workspaceId}                                → {notebookId, jobInstanceId, agentItemId}
GET  /api/pbi-fixer/fca/job-status                             query: {workspaceId, itemId, jobInstanceId}       → {status, livyTail?}
```

### Pinned assets

- `Backend/data/templates/00_Deploy_FCA.ipynb` — pinned to release `2026.4.x` (latest at WS-FCA kickoff). SOURCE.md records the upstream commit hash + checksum.
- `Backend/data/templates/02_Create_DataAgent.ipynb` — pinned, used in optional Step 10.
- `Backend/data/templates/fca_workspace_icon.png` — optional FCA workspace icon, applied in Step 3.
- `Backend/data/templates/fca_metrics_compat.json` — not applicable here (FCA does not depend on the Capacity Metrics App), but reserve a `Backend/data/templates/fca_release_compat.json` to track which upstream FCA release this wizard understands. Surface "wizard expects FCA release X, latest is Y, please update wizard" if the upstream README's `<release>` marker drifts.

### Cross-cutting concerns

- **Azure ARM token reuse.** All ARM calls in Steps 0–2, 5, 6 use the user's signed-in identity via the existing OBO chain — no SPN required for the subscription-side work. Confirm the OBO grant for `https://management.azure.com/.default` is part of the workload's required scopes; if not, add it. (FUAM doesn't need ARM scope; this is new for FCA.)
- **OneLake shortcut auth.** Step 5 uses Organizational Account (signed-in user OAuth) per the upstream guide. SPN is not exercised in the upstream — keep parity to avoid surprises. If the user wants SPN, they can swap the connection credentials in Fabric Settings after the wizard finishes.
- **FinOps Hubs interop.** When the Step 0 toggle "I already have FinOps Hubs" is on, Step 1+2 collapse into a single "Provide your existing FinOps Hubs storage URL + ingestion container path" form. The shortcut in Step 5 then points at `<finopsHubsContainer>/Costs` instead of `fca/fca`. Documented in the upstream Deploy.md "Costs in the ingestion container" note.
- **Idempotency.** Re-running any step should be safe: workspace creation handles `409 already exists` by adopting the existing workspace; export creation uses `PUT` with the same name (overwrites the export config but preserves history); notebook upload uses Items API `updateDefinition` (overwrite); shortcut creation surfaces `409` as "already exists, reusing".

### Risks / open questions

- **Cost Management Exports API maturity.** The `Microsoft.CostManagement/exports` REST endpoint exists and is documented (`api-version=2023-08-01`), but error messages are notoriously cryptic when the source scope is misconfigured. Pre-validate the scope by hitting `GET .../providers/Microsoft.CostManagement/dimensions?...` first and surfacing a clear "this scope doesn't have cost data" if 404.
- **Storage account region != Fabric capacity region.** Cross-region shortcuts work but pay egress. Surface a warning in Step 1 if the picker would cross regions.
- **Permissions for export-creation propagation.** `Cost Management Contributor` granted via Entra group can take 5–10 min to propagate. If Step 2 fails with 403, surface the "wait 10 min and retry" recovery path explicitly.
- **Reservation export costs.** Reservation exports are cheap but not free; surface the "Reservations are still in Preview" caveat from the upstream Reservation.md guide.
- **Data Agent prerequisites.** Fabric Data Agent needs Copilot enabled + a sufficient capacity SKU. Skip Step 10 silently if pre-flight fails — don't block deployment.

### Ship order

1. **Step 4 only** (notebook upload + run) — same shape as WS-FUAM Step 4. Ships first because it validates the Items API jobs path with a different notebook (independent verification of WS-FUAM's risk).
2. **Step 1 + Step 2** (storage + cost export) — biggest new technical surface (ARM Cost Management API). Ship behind a feature flag, validate against a personal subscription first.
3. **Step 5** (shortcut creation) — once Step 2 produces real data, prove the OneLake shortcut path works end-to-end.
4. **Step 8 + 9** (initial load + finalize) — end-to-end happy path, the user can now deploy a "core only" FCA without optional modules.
5. **Step 0** (pre-flight) — built once we know which checks actually save users from failures we observed in 1–4.
6. **Step 3 + Wizard UI** — workspace creation + stitching all the steps together with state persistence.
7. **Step 6 (Reservations)** — first optional module.
8. **Step 7 (Quotas)** — second optional module (connection-binding pattern).
9. **Step 10 (Data Agent)** — last, the pre-flight is non-trivial.

### Non-goals (v1)

- **Programmatic Cost Management Export creation outside the user's directly-owned scopes.** Billing-account-scoped exports require special tenant-level permissions; we surface the manual path with deep links if the user picks a billing-account scope and lacks rights.
- **Auto-enrolment in FinOps Hubs.** If the user wants the full FinOps Hubs stack, point them at the FinOps Toolkit; we only consume the storage they've already set up.
- **Multi-subscription FOCUS aggregation in one wizard run.** The upstream pipeline supports multi-subscription via folder-structure conventions, but our wizard ships single-subscription per run (re-run with a different subscription to add more).
- **Custom report theming / page additions.** The FCA core report is the deliverable; advanced customisation is out of scope.
- **Update flow.** Same as WS-FUAM v1 — re-run the deploy notebook by hand for now; full update wizard in v2.

---

## WS-LOCAL — Local ↔ online round-trip (PBIP / PBIX bridge, NEW, P1)

Today every fixer writes back to Fabric via REST (`updateDefinition`). The user also wants to **persist the same edits to a local PBIP/PBIX folder** — either as the initial seed of a new local project, or as an overwrite of an existing file at the matching display-folder path. Goal: a single "Sync local copy" button that bridges the in-workload edit and the on-disk artifact without leaving the Fixer.

### Why a dedicated workstream
- The Fixer currently has **only one persistence target** (the live Fabric item). Source-control workflows, offline edits, and PBI Desktop round-trips all need a local mirror.
- Two different mirror modes need to coexist: **PBIP** (folder of TMDL + PBIR JSON, ideal target for our edits — same format we already produce) and **PBIX** (single binary file, harder to write but what users actually open in Desktop).
- Display-folder structure in TMDL/PBIR (e.g. `tables/Sales/measures/Revenue.tmdl`) maps naturally onto **filesystem subfolders**, so we don't need an extra mapping layer — but we do need an explicit overwrite policy per file.
- Browser sandboxing means filesystem access uses the **File System Access API** (Chromium-only `showDirectoryPicker`), which has its own permission and persistence model — not just a `<input type=file>`.

### Modes

| Mode | Local format | Trigger | Behaviour |
|---|---|---|---|
| **Seed** | PBIP folder | First time the user picks an empty (or non-PBIP) folder | Write the full TMDL + PBIR tree from the live item's current `getDefinition` payload. Creates `<displayName>.SemanticModel/` and `<displayName>.Report/` subfolders with `definition.pbism` / `definition.pbir` markers + `definition/` subfolder containing all parts. |
| **Overwrite (matched)** | PBIP folder | User picks an existing PBIP folder whose `displayName` matches the connected item | For each part path the fixer touched, write the new content into the matching subfolder file. Files at unrelated display-folder paths stay untouched. |
| **Overwrite (mismatched)** | PBIP folder | Folder displayName ≠ item displayName | Block with a confirm dialog: "Folder is `Sales.SemanticModel`, item is `Marketing`. Continue and rename folder?" — only proceed on explicit confirm. |
| **Export PBIX** | Single `.pbix` file | Optional secondary button | Call Fabric `exportItem` (Imports API equivalent) for a `.pbix` payload, then prompt the browser to save the file. **Read-only:** we don't write back into a PBIX (no API for in-place PBIX patching). |

### UI shape
- New toolbar row in the Fixer header: **"Local copy"** group with three controls:
  1. `[Pick local folder]` — calls `showDirectoryPicker({mode: 'readwrite', id: 'pbi-fixer-local'})`. Persists the handle in IndexedDB so re-opens don't re-prompt.
  2. `[Sync to local]` — disabled until a folder is picked. Tooltip shows the resolved mode (Seed / Overwrite-matched / Overwrite-mismatched). On click: confirm dialog showing the file diff (added / modified / unchanged), then write.
  3. `[Export .pbix]` — independent, doesn't require the folder handle.
- Status chip next to the folder name: `Linked: <folderName>` with a small ↻ to re-pick. Clears when the user closes the workload.

### Display-folder → subfolder mapping
PBIR/TMDL already encodes display folders directly in the part path:
```
definition/tables/Sales.tmdl
definition/tables/Sales/measures/Revenue.tmdl       ← display folder = "Sales/measures"
definition/tables/Sales/columns/Amount.tmdl
definition/expressions.tmdl
report/definition/pages/Page1/visuals/Visual_abc.json
```
The local writer mirrors this 1:1 — every `/` in the part path becomes a `\` on Windows. No separate "display folder" config: the part path **is** the display folder.

### Step contract (Sync click)

| # | Step | Detail |
|---|---|---|
| 1 | **Resolve folder mode** | Read `<folder>/definition.pbism` (model) or `definition.pbir` (report). If missing → Seed mode. If present → parse the inner `displayName` and compare against the connected item — Overwrite-matched / Overwrite-mismatched. |
| 2 | **Pull live definition** | `POST /v1/workspaces/{wsId}/items/{itemId}/getDefinition?format=TMDL` (model) or `?format=PBIR-Legacy` (report). LRO poll → base64 parts. |
| 3 | **Diff against disk** | For each part: read existing file contents (if any), compare to fresh payload. Classify as `added` / `modified` / `unchanged` / `removed-on-server`. |
| 4 | **Show diff dialog** | List of files grouped by status. Default: write `added` + `modified`, leave `removed-on-server` alone (user can explicitly check "delete server-removed files locally"). |
| 5 | **Write parts** | For each accepted file: `await dirHandle.getDirectoryHandle(seg, {create:true})` recursively, then `getFileHandle(name, {create:true})` + `createWritable()` + `write(decodedBytes)` + `close()`. |
| 6 | **Write `.pbip` shortcut file** | Only on Seed: write `<displayName>.pbip` at the folder root pointing at the SemanticModel + Report subfolders, so the user can open it in Desktop directly. |
| 7 | **Persist handle** | Save the `FileSystemDirectoryHandle` in IndexedDB keyed by `wsId+itemId` so the next session re-uses it (browser still re-prompts for permission once per origin per session, but the picker is skipped). |

### Backend involvement
**Minimal — most of this is browser-side.** Backend just needs:

```
POST /api/pbi-fixer/local/get-definition         body: {workspaceId, itemId, itemType}    → {parts: [{path, payload (base64)}]}
POST /api/pbi-fixer/local/export-pbix            body: {workspaceId, itemId}              → {downloadUrl}  (LRO behind the scenes)
```

The `get-definition` endpoint is a thin proxy over the Fabric `getDefinition` LRO — we already use that pattern in WS-MON / WS-FUAM. Reusing the existing handler is fine; new endpoint just exists so the local-sync UI doesn't have to know about Fabric REST shapes.

### File System Access API constraints (call out explicitly)

- **Chromium-only** (Edge, Chrome, Brave). **Not available in Firefox or Safari.** Surface a warning chip + fallback "Download as zip" button so non-Chromium users can still save the seed.
- **User-activation required** — `showDirectoryPicker()` must be called from a click handler, not async-after-fetch.
- **Permission grants are per-session** — even with a persisted handle, browsers re-prompt once per origin per session. We surface a single "Grant access" toast on first sync after restart, then proceed silently.
- **Cross-iframe:** the Fabric workload runs inside a Fabric Shell iframe. Verify that `showDirectoryPicker` works from the workload origin — Fabric Shell uses `allow="..."` on its iframe and we may need to request the `directory-upload` policy permission explicitly. If blocked, fallback path: open a popup window from the workload that hosts the picker on our origin, then `postMessage` the handle back. (Handles can be transferred via `postMessage` with `transfer:[handle]` per spec — verify in a spike before committing to the popup path.)
- **OneDrive-synced folders work fine.** Test with a OneDrive-synced repo path so users can pick `repos/PBI-Prototyping/<Report>/` and have the seed land in their existing source-control folder.

### Risks / open questions

- **PBIP version compatibility.** PBI Desktop is picky about TMDL version markers in `definition.pbism`. We must write the same `version` string the live item is currently on, otherwise Desktop refuses to open. Pull from the live `getDefinition` payload, don't hardcode.
- **Bidirectional sync is out of scope (v1).** This workstream is **online → local only**. Picking a folder with newer local edits and pushing them back to Fabric is a separate WS (WS-LOCAL-PUSH) — flag in the diff dialog if local files are newer than the server timestamp, but don't act on it.
- **Binary parts.** Theme JSON, custom-visual `.pbiviz`, embedded images — most are already in the `parts` array as base64 and round-trip fine. Verify nothing decodes to corrupted bytes (write `arrayBuffer`, not string).
- **Race against concurrent edits.** If the user runs a fixer in the workload while a sync is in flight, the diff in Step 3 is stale. Lock the toolbar during sync — disable other fixer Apply buttons. Re-enable on success/fail.
- **Removing files from the server.** PBIR/TMDL part lists shrink when an object is deleted. Default UX: leave the local file alone (less destructive). Behind a checkbox: "Mirror deletions" — also deletes locally.

### Ship order

1. **PBIP Seed only** — pick folder + write fresh payload. Single happy path, no diff dialog yet (just "Wrote N files"). Validates the FSA API end-to-end inside the Fabric iframe.
2. **Folder-handle persistence + permission re-grant flow** — IndexedDB store, silent re-use, explicit re-grant toast.
3. **Diff dialog + Overwrite-matched mode** — added/modified/unchanged classification, default-safe writes.
4. **Overwrite-mismatched confirm + rename guard.**
5. **PBIX export button** — independent track, can ship in parallel.
6. **Firefox/Safari fallback** — zip download via JSZip in browser.
7. **Cross-iframe popup fallback** — only if the in-iframe `showDirectoryPicker` test in Step 1 fails.
8. **WS-LOCAL-PUSH spike** — separate workstream, push direction.

### Non-goals (v1)

- **Local → online push.** One-way only; push lands in WS-LOCAL-PUSH.
- **Conflict resolution UI.** If a local file differs from server, surface it as "modified" with no merge view — user picks overwrite or skip.
- **Desktop launch automation.** We write the `.pbip` file; opening it in Desktop is the user's click.
- **Git integration.** The folder may be a git repo, but we don't shell out to git. User commits manually.
- **Edit-in-place PBIX.** No API supports patching a PBIX without a full rebuild. Export only.

---

## WS-USAGE — Usage Metrics history capture (NEW, P2)

Power BI's built-in **Report Usage Metrics Model** is a hidden per-workspace semantic model that retains only **30 rolling days** of activity. Long-term retention requires snapshotting it externally. A working reference implementation already exists at [Fabric-Notebooks/Usage Metrics Snapshot.ipynb](Fabric-Notebooks/Usage%20Metrics%20Snapshot.ipynb) — pulls each table via `EVALUATE` over XMLA with `sempy.fabric`, appends to Delta with `mergeSchema=true`, applies retention.

### Three-tier solution menu (offer all, recommend by data volume)

| Option | What it captures | When to recommend | Complexity |
|---|---|---|---|
| **A. Save the notebook only** | Per-workspace usage metrics (Views/Reports/Users/Dates/DistributionMethods/Platforms) | User wants to roll their own; one workspace; no Fabric admin rights | Trivial — just expose a `[Download notebook]` button |
| **B. Wizard: deploy + schedule the snapshot notebook** | Same as A, but managed | One or a few workspaces; user owns those workspaces; wants set-and-forget | Medium — same pattern as WS-MON/WS-FUAM |
| **C. Tenant-wide Activity Events ingestion** | Every read/edit/share event tenant-wide via [Admin Activity Events API](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events) (`/admin/activityevents`) — strict superset of usage metrics, plus admin/sharing/CRUD events | Fabric admin user; org-wide reporting; needs richer than just "views per report" | Higher — needs SPN with `Tenant.Read.All`, daily incremental window logic, ~30 event types to model |

The user's question — "is there not something better?" — points at **Option C** as the proper answer: Activity Events covers a strict superset of what the usage-metrics notebook captures (it includes `ViewReport`, `ViewDashboard`, `ExploreSemanticModel` plus 30+ other events), is tenant-wide instead of per-workspace, has 90 days of native retention (vs 30), and doesn't require enabling the per-report usage metrics dataset. **Recommend C for admins, B for everyone else, A as the escape hatch.**

### WS-USAGE-A — Notebook download button (ship first, smallest surface)

Add a fixer entry under the Workspace tab: **"Capture usage metrics history"**. Clicking it surfaces a side-panel with:
- The pinned notebook content (read-only preview, syntax-highlighted)
- `[Download .ipynb]` button — saves [Fabric-Notebooks/Usage Metrics Snapshot.ipynb](Fabric-Notebooks/Usage%20Metrics%20Snapshot.ipynb) verbatim from `Backend/data/templates/Usage_Metrics_Snapshot.ipynb`
- Inline how-to (3 steps: import, set `WORKSPACE_ID`, schedule daily after 04:00 UTC)
- A "Want this fully managed? → switch to wizard mode" link that pivots to Option B

Backend: zero. Just serve the file.

### WS-USAGE-B — Deploy + schedule wizard (managed mode, second ship)

Same skeleton as WS-MON, smaller scope. Operates against any workspace the user picks (default: current workspace).

| # | Step | Backend call |
|---|---|---|
| 1 | **Pick target workspace + Lakehouse** | `GET /v1/workspaces` + filter to capacity-backed (XMLA required) → list Lakehouses inside the chosen workspace; offer "Create new Lakehouse `usage_metrics_lh`" if none exists |
| 2 | **Verify Report Usage Metrics Model exists** | Query semantic models by name (`Report Usage Metrics Model`); if missing, surface the canonical "click *More options → View usage metrics report* on any report once" instruction with a deep link — we cannot create this model via API, only PBI Web UI can trigger its first creation |
| 3 | **Upload + parameterise the snapshot notebook** | Upload [Fabric-Notebooks/Usage Metrics Snapshot.ipynb](Fabric-Notebooks/Usage%20Metrics%20Snapshot.ipynb) via Items API, with `WORKSPACE_ID` substituted into the first cell before upload (textual replacement before base64-encoding) |
| 4 | **Attach Lakehouse + initial run** | Set default Lakehouse on the notebook; trigger one synchronous run via `POST .../jobs/instances?jobType=RunNotebook`; verify all 6 tables landed |
| 5 | **Schedule daily 05:00 UTC** | `PATCH .../items/{notebookId}/schedules` (default 05:00 UTC, configurable; intentionally after the source's 03:00 UTC refresh + 1h buffer) |

Backend endpoints under `/api/pbi-fixer/usage-metrics/*` mirror the WS-MON shape (5 endpoints).

### WS-USAGE-C — Tenant Activity Events ingestion (the "better" answer)

Different beast — tenant-scope, admin-only. Ship as a separate sub-wizard with its own pre-flight.

- **SPN requirement**: dedicated SPN with `Tenant.Read.All` (Admin API) + member of "Power BI service admins" or "Fabric admins" security group. Documented in upstream [activity-events docs](https://learn.microsoft.com/en-us/power-bi/enterprise/service-admin-auditing#use-the-rest-api). Surface clear "you must be an Entra Global Admin or Cloud App Admin to consent this" warning.
- **Pinned notebook**: `Backend/data/templates/Activity_Events_Snapshot.ipynb` — daily incremental over `/admin/activityevents` with 24h window (the API caps at 1 day per call), idempotent merge into Delta keyed on `(Id, CreationTime)`, optional 90-day backfill (the API max).
- **Schema**: ~40 columns covering all event types — model as one wide append-only `activity_events_raw` table + projections for the common slices (`activity_events_views`, `activity_events_edits`, `activity_events_shares`).
- **Deploy + schedule wizard** identical shape to WS-USAGE-B but in a dedicated admin workspace, with the SPN secret bound to a Fabric cloud connection (same encrypted-at-rest pattern as WS-FUAM).
- **Optional**: ship a companion `Activity_Events_Report.pbit` (semantic model + report) covering the 90-day window with drill-throughs by user / capacity / item — but this is a v2 ask, not v1.

### Pinned assets

- `Backend/data/templates/Usage_Metrics_Snapshot.ipynb` — synced from [Fabric-Notebooks/Usage Metrics Snapshot.ipynb](Fabric-Notebooks/Usage%20Metrics%20Snapshot.ipynb), with `WORKSPACE_ID = "<your-workspace-guid>"` left as the placeholder for option A and substituted at upload time for option B.
- `Backend/data/templates/Activity_Events_Snapshot.ipynb` — to be authored as part of WS-USAGE-C (a working reference doesn't yet exist locally; build from upstream API docs).
- `Backend/data/templates/Activity_Events_Report.pbit` — v2 only.

### Risks / open questions

- **`Report Usage Metrics Model` is not API-creatable.** Both A and B need the user to click "View usage metrics report" once on any report in the workspace before the model exists. No workaround — surface the instruction inline. (Option C bypasses this because Activity Events doesn't depend on the per-report model.)
- **XMLA endpoint required** for option B (notebook uses `EVALUATE` over the model). Pre-flight in Step 1: filter workspaces to those backed by Premium / PPU / Fabric capacity.
- **Activity Events API window quirks.** Returns max 5000 events per page with continuation tokens, max 24h per request. Notebook needs paging + `continuationUri` handling — flag in WS-USAGE-C ship checklist.
- **Schema drift in usage metrics.** Microsoft has historically added columns (e.g. `ViewerType`, `Browser`) without notice. The notebook already uses `mergeSchema=true` to absorb this; document that downstream queries must tolerate `NULL`s for older snapshot rows.
- **Capacity cost.** Daily Spark notebook run is ~F2-minutes-per-workspace. Mention in the wizard: if user has 50 workspaces, prefer Option C (one tenant-wide pull) over 50 instances of Option B.

### Ship order

1. **A** — notebook download button. One day of work, immediate value.
2. **B** — deploy + schedule wizard. Same pattern as WS-MON, refactor opportunity to extract a `deployScheduledNotebook(workspaceId, notebookTemplate, params, schedule)` helper that all three wizards can share.
3. **C** — Activity Events sub-wizard. Builds on B's helper; adds SPN + cloud-connection management.
4. **C v2** — companion report `.pbit`.

### Non-goals (v1)

- **Building our own scoring / anomaly model on top of the captured data.** This workstream just lands raw data; analytics is a separate ask.
- **Automated SPN provisioning** for option C — same hard policy boundary as WS-FUAM.
- **Migration of historical data from existing user-rolled snapshot tables** — we land into fresh `usage_metrics_*` tables; importing pre-existing data is the user's call.
- **Real-time / streaming.** Daily batch only. If the user wants real-time, point them at Workspace Monitoring's eventhouse (which captures semantic-model query events live, complementary to usage metrics).

---

## Explorer / Tab gaps (parity backlog)

Batch 1 (v0.59), Batch 2 (v0.60) and Batch 3 (v0.61) shipped — see [CHANGELOG.md](CHANGELOG.md). Remaining items below stay deferred — each is a self-contained workstream rather than a localized UI tweak.

### Model Explorer
- **Multi-model support** — types support it (`ModelData.datasetName?`), but the picker, connection bar, and shared `ResolvedIds` state are all keyed off a single workspace+dataset pair. Would require a tab/list UI for switching between loaded models (or side-by-side compare) plus careful eviction of pending edits when the active model changes. Not on the IBCS critical path.
- **Scan mode (BPA badges on tree items)** — currently scans live on the dedicated BPA pages (Model BPA / Report BPA), and `buildModelTree` accepts a `_scanResults` arg that's still ignored. Wiring would mean: (a) trigger a scan from the explorer toolbar (reusing `modelBpaApi.runScan`), (b) plumb the results into the tree builder via the existing `scanResults` parameter, (c) overlay rule-violation count badges on each tree row. Deferred until BPA scans become a "live alongside the explorer" UX rather than a separate page.

### Report Explorer
- **Per-visual quick-fix scoping** ⏸ — v0.61 ships report-wide quick-fix buttons in the visual props pane (filtered by `appliesTo: VisualType[]` on each `backendFixer({...})` entry), but applying still scans the whole report. True single-visual scoping needs a `targetPath?: {page, visual}` arg on `/api/pbi-fixer/fixers/apply` plus per-handler scope filtering in `pbi_fixer_handlers.py`. Deferred as a follow-up workstream.
- **Drag-and-drop page reorder** ⏸ — v0.61 attempted HTML5 drag, v0.63–0.65 attempted pointer events with `setPointerCapture` and global window listeners. **All variants blocked by the Fabric workload-iframe host** (Fabric Shell either intercepts dragstart and throws "UnknownError: Could not handle exception", or swallows pointermove/pointerup before they reach the iframe). Backend endpoint `POST /api/pbi-fixer/report/pages/reorder` works correctly when called directly. UI removed in v0.66; reorder still possible via direct REST call. **Future approach:** explicit "Reorder pages" dialog with up/down arrow buttons on each page row (no native drag), or per-page "Move up/Move down" context-menu actions. Will not need any browser drag APIs.
- **Scan mode badges in report tree** — same shape as the model-explorer scan-mode item: `scanResults` is an empty `useState`, badge slots exist in `buildReportTree` but never get populated. Fix is parallel to the model side — wire up the existing `reportBpaApi.runScan` from the toolbar and feed the results into the tree builder. Deferred so model + report scan-mode ship as one consistent UX, not two divergent halves.

### Other tabs not yet planned as standalone WSes
None — all originally-identified tabs are covered.

---

## Open workstreams

### WS-N — Integration sweep (remaining work)
- [ ] Final smoke pass after each integration batch
- [x] LLM-backed translation propose endpoint (replaces glossary stub) — `Backend/src/api/agenthub_controller.py` (`_llm_translate_batch` + `pbi_fixer_translations_propose`); user-supplied `glossary` forwarded as preferred terminology, batched single Copilot call per culture, JSON response, fallback to source on failure. Frontend payload shape unchanged.

### WS-S — Multi-Model & Multi-Report editing (NEW, planned)

**Goal.** Lift the single-`(workspace, dataset|report)` invariant currently enforced by `PbiFixerPage` so the user can have several models / reports loaded side by side, scan all of them in one go, and apply fixers across the set.

**Today's constraints (single-target).**
- `PbiFixerPage` props expose exactly one `workspaceId`, `datasetId`, `reportId`. Every page (`Model`, `Report`, `ModelBpa`, `ReportBpa`, `Memory`, `Translations`, `Fixer`, `SempyRunner`, …) is keyed off these via `remountKey = ${activeNav}::${workspaceId}::${datasetId}::${reportId}`.
- Pending edits and BPA findings live in component-local `useState`. Switching the dataset evicts everything.
- The Editor Tabs strip already supports multiple PBI Fixer tabs (one per `nav` key), but they all share the same connection bar.

**Phase 1 — Multi-target session (read-only scans).** _Smallest useful slice; ships first._
1. **Connection bar** — extend the dataset / report picker to a multi-select chip-style control. Selected items go into a new `targets: TargetRef[]` state at the `PbiFixerPage` level, where `TargetRef = { workspaceId; datasetId?; reportId?; displayName }`.
2. **Active vs scope** — keep `(workspaceId, datasetId, reportId)` as the *active* target (drives the existing single-model views). Add a separate `scope` (the full target list) consumed by new bulk pages.
3. **New nav entry: "Bulk BPA"** (group `modelTools`) — runs `runModelBpa(loadModelData(...))` for every dataset in `scope` in parallel; aggregates findings into one grid with an extra `dataset` column. Same for Report BPA via a sibling page.
4. **No write-back yet** — fixers stay scoped to the active target.

**Phase 2 — Bulk apply.**
5. New backend endpoint `POST /api/pbi-fixer/fixers/apply-bulk` accepting `targets: [{workspaceId, datasetId, fixerId, args, scanOnly}]`, fanning out to the existing `/fixers/apply` per target with bounded concurrency (3-5).
6. UI: the existing scan-result panel in the Model / Report explorer gains an "Apply across selection" button visible when `scope.length > 1`. Per-target progress + per-target errors surface in a results table (success / partial / failed).
7. Idempotency: every fixer must already be safe to re-apply (current contract). Add a confirmation dialog listing the targets before fan-out.

**Phase 3 — Side-by-side editing.**
8. Introduce a "Compare" view that lets the user split the editor area horizontally and pin a different `(target, navKey)` to each side — leveraging the existing Editor Tabs *group* abstraction (groups already exist in state, only one is rendered today).
9. Per-tab connection state: each tab carries its own `targetRef` instead of inheriting from a single page-level value. The connection bar becomes per-tab when more than one group is open.
10. Pending-edit isolation: move the `pendingEdits` and BPA `findings` state from the page components into a small per-target store (`Map<targetKey, PerTargetState>`), keyed by `${workspaceId}::${datasetId|reportId}`, so edits survive a `remountKey` change and so the Compare view can show two states at once.

**Risks / open questions.**
- Token / capacity load: parallel scans hit XMLA + Fabric REST hard. Cap concurrency at 3 and surface a "scanning N/M" progress chip.
- Quota for bulk write-back — Fabric `updateDefinition` LROs are not free; bulk apply must serialise per-target writes inside one fan-out worker.
- Tab UX with N targets — needs a target switcher (combobox or sub-tab strip) inside the connection bar before the tab strip itself becomes the switcher.
- Sharing pending edits between the active target and the bulk grid: one direction only (bulk → single-target), to keep the mental model simple.

**Cut from v1.** Cross-workspace selection (today: one workspace per session); per-target token scoping (all targets share the signed-in user's Fabric token); cross-target rule customisation (every target uses the same global rule set).

---

## Architecture decisions

| Decision | Choice | Rationale |
|---|---|---|
| UI Framework | React + FluentUI v9 | Matches Developer Hub Frontend stack |
| API Layer | Fabric REST + PBI REST | Browser-compatible (Python used sempy_labs/XMLA/TOM) |
| Auth | Token passed as prop | Developer Hub handles OAuth, passes token down |
| State Management | React useState/useCallback | Component-local; may migrate to Redux later |
| SM Write-back | TMDL round-trip via backend | `getDefinition?format=TMDL` → mutate → `updateDefinition` (LRO). Pattern shipped in WS-G v0.40, reused in WS-E v0.41 |
| Report Write-back | Fabric REST `updateDefinition` | Direct JSON patching of PBIR definition parts |

---

## Single-Owner Files (parallel DEV, last-edit-wins merge)

Every workstream only edits files listed under its **Owns** section. Shared files split between two owners:
- **WS-A** owns shell + page-slot surface (`PbiFixerPage.tsx`, `PbiFixerNav.tsx`, page stubs, nav metadata, theme).
- **WS-N** owns integration/shared-assets (`utils/version.ts`, `Frontend/package.json`, final cross-page wiring).

All other workstreams add to their own files + append-only exports from `types/shared.ts`. Plain `git pull --rebase` + push from each chat is enough — no worktrees, no feature branches. If two chats collide on the same line: last edit wins; re-run the loser workstream against the merged file.

**Versioning:** only WS-N updates `utils/version.ts`. Individual chats do not touch shared version files.

---

## Testing — Playwright per chat window
- **Reuse the existing browser tab.** Auth state, dev-mode toggle, cert trust live in that tab — do NOT open a new tab per chat.
- **After hard reload / cache clear**, the Fabric workload "Continue" auth popup reappears. Click it once before touching the `pbifixer` iframe, otherwise tokens come back empty.
- Parallel chats coordinate on the same tab; if another chat is driving it, wait — Playwright MCP only exposes one Chromium profile.

---

## Parallel Development Guide — copy into each new chat

> "You're working on **WS-X** of `Developer Hub/Frontend/src/components/PbiFixer/PLAN.md`.
> Read that file, then the `## WS-X` section. Only edit files listed under **Owns**.
> Never touch `PbiFixerPage.tsx` (WS-A only) or shared files like
> `utils/version.ts` / `Frontend/package.json` (WS-N only).
> When done: do **not** change the shared version file; commit
> `[WS-X] <summary> (target vX.Y)` and tick acceptance checkboxes in PLAN.md.
> If you hit a conflict with another chat's edits, rebase and let last edit win — your scope is disjoint so no real logic should collide."

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green light from Alexander
- **Script Runner / arbitrary code execution** — removed May 2026 (was WS-K).
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param reserved, no UI)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
