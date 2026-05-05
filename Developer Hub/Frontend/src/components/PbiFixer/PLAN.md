# PBI Fixer — TypeScript Port Plan

> Forward-looking work only. For shipped workstream history, see [CHANGELOG.md](CHANGELOG.md).
>
> **Source:** `c:\Users\alkorn\repos\pbi_fixer\src\` (Python notebook, ~12,000 lines)
> **Target:** `c:\Users\alkorn\repos\Fabric_Developer_Hub\Developer Hub\Frontend\src\components\PbiFixer\`
> **Version policy:** stay on `v0.x`. Bump patches (`v0.5` … `v0.99`). **Never auto-bump to `v1.0`** — Alexander gives explicit green light.

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
| 6 | **Fix IBCS Variance** | `variance()` | XL | 771-line original; port in 3 PRs (visual props / measure attach / error bars) |
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

### WS-T — May 5 2026 user-reported bug batch (NEW, in flight)

Captured from a single bug report so we don't lose them between sessions. Sub-tasks intentionally fine-grained so each can ship + commit independently.

- **T1. "Could not handle exception: undefined" dialog still shows.** The Fabric host throws this from its own `callItemGet` path because dev-loaded workload items don't exist as real Fabric items (`workloadPayload` is null). Investigate: (a) can we register a stub item in dev mode so `callItemGet` resolves; (b) can the workload swallow the host's `unhandled-error` postMessage; (c) document as a known dev-only quirk in CHANGELOG if neither (a) nor (b) is feasible.
- **T2. Tab title stuck on "Loading…".** When the Fabric host opens the workload it sets the tab title to "Loading…" and never updates. Investigate the workload-side `setItemTitle` / `notifyItemTitle` API in `@ms-fabric/workload-client` and call it from the PBI Fixer mount once we know the item / page is ready. Verify in the Fabric tab strip and the browser tab title.
- **T3. Revert hero + KPI strip on Model + Report explorers.** User feedback: too many headings + the big number tiles take too much vertical real-estate above the workspace. Restore `ModelExplorer.tsx` and `ReportExplorer.tsx` to the pre-redesign render (toolbar directly under the connection bar, no hero, no KPI strip, no info MessageBar). The MessageBar import added in the same commit becomes dead — remove it too.
- **T4. Model BPA SLL run still fails with arch error.** SLL sidecar still returns `501 SLL sidecar host arch 'aarch64' is not supported. sempy_labs requires x86_64`. The sidecar Dockerfile / compose target must pin `linux/amd64` so x86_64 wheels work even on Apple Silicon dev hosts. Confirm host arch via `docker inspect developerhub-sll-sidecar-1 --format '{{.Architecture}}'` and `--platform=linux/amd64` in the build command + compose service definition. Verify with Playwright that "Run" succeeds on the demo workspace.
- **T5. Memory Analyzer not working.** Same root cause as T4 if it goes through the SLL sidecar (`sempy_labs.vertipaq_analyzer`). Test in Playwright; fix follows from T4 if the chain is the sidecar. Surface an actionable error if the underlying call fails (today the page just looks empty).
- **T6. Report BPA → SLL native (deferred).** Park behind T4 + T5 — only flip from the TS engine to `sempy_labs.report.run_report_bpa` once the existing SLL Model BPA + Vertipaq paths run cleanly. When unblocked: add `/sll/report-bpa` in `SllSidecar/app.py` mirroring the `run_model_bpa` monkey-patch + `/sll/vertipaq` plumbing.
- **T7. Workspace + item name should persist across sub-tab switches.** Today the connection bar in `PbiFixerPage` is component-local — every new PBI Fixer sub-tab (Model, Report, Model BPA, …) starts with empty pickers. Lift `(workspaceId, datasetId, reportId, *Name)` into a small context (or sessionStorage-backed singleton) shared by all PBI Fixer pages and seeded from the previously-active selection. Acceptance: open Model → load workspace + dataset → switch to Report → workspace stays, dataset stays where applicable, only the per-scope picker (report vs dataset) flips.
- **T8. Cannot open the DevHub Dashboard item from the workspace folder.** Reproduce: workspace → click the Developer Hub Dashboard tile → expected: the workload opens. Investigate the item registration manifest (`Backend/manifests/Org.DeveloperHub.DeveloperHubDashboard.json` or equivalent) — likely missing `editor` route / `frontendBaseUrl` for the deployed item shape vs the dev-mode shape. Confirm what URL the Fabric host tries to navigate to and whether the workload claims it.
- **T9. DevHub Dashboard item icon is wrong.** The "Developer Hub Dashboard (preview)" tile in the new-item gallery shows a generic / wrong glyph. Replace with the same `developerHub.png` (or new SVG) used in the topbar / About page. Likely lives in the item manifest's `icon` / `iconSmall` field. Verify in the gallery, the workspace list, and the open-item header.

Tracker:
- [x] T1 dialog — `callItemGet` no longer routes empty exceptions through `handleException` (workload v1.37 / PBI Fixer v0.78)
- [x] T2 loading title — `agenthub.tab.onInit` falls back to "Developer Hub Dashboard" instead of `{}`
- [x] T3 revert hero + KPI — Model + Report restored to toolbar-first render, MessageBar/useMemo removed
- [x] T4 SLL arch fix — `platform: linux/amd64` pinned on the `sll-sidecar` compose service
- [x] T5 Memory Analyzer — fixed transitively by T4 (same SLL sidecar path)
- [ ] T6 Report BPA SLL flip (gated on T4 + T5 verify in Playwright)
- [x] T7 workspace + item context — `PbiFixerPage` now seeds + persists the connection bar to `sessionStorage["pbiFixer.connection.v1"]`
- [x] T8 dashboard item from workspace folder — likely fixed transitively by T1 (host dialog was blocking the item editor mount); needs Playwright re-verify
- [x] T9 dashboard item icon — `Product.json` createExperience card icon swapped from `dial.png` to `developerHub.png`

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
