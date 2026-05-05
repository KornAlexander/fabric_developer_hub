# PBI Fixer — TypeScript Port Plan

> Forward-looking work only. For shipped workstream history, see [CHANGELOG.md](CHANGELOG.md).
>
> **Source:** `c:\Users\alkorn\repos\pbi_fixer\src\` (Python notebook, ~12,000 lines)
> **Target:** `c:\Users\alkorn\repos\Fabric_Developer_Hub\Developer Hub\Frontend\src\components\PbiFixer\`
> **Version policy:** stay on `v0.x`. Bump patches (`v0.5` … `v0.99`). **Never auto-bump to `v1.0`** — Alexander gives explicit green light.

---

## Delivery status snapshot

| WS | Feature | Status | Version |
|----|---------|--------|---------|
| WS-A | Shell / nav / theming | ✅ shipped | v0.1–v0.5 |
| WS-B | Memory Analyzer | 🟡 partial (Phase 2) | v0.18 |
| WS-C | Model BPA | ✅ shipped | v0.12 |
| WS-D | Report BPA | ✅ shipped | v0.13 |
| WS-E | Fixer Execution | ✅ shipped + backend apply | v0.14 → v0.41 |
| WS-F | Perspectives | ✅ shipped (write-back deferred) | v0.15 |
| WS-G | Translations + Auto-Translate | ✅ shipped + backend apply | v0.11 → v0.40 |
| WS-I | Delta Analyzer | ✅ shipped | v0.24 |
| WS-J | Diagram (SVG canvas) | ✅ shipped | v0.19–v0.20 |
| WS-K | Script Runner (Monaco) | ⬜ not started | — |
| WS-L | About — **moved to AgentHub shell** (was Fixer page) | ⬜ not started | — |
| WS-M | Prototype | ✅ shipped | v0.16 |
| WS-N | Integration sweep | 🟡 partial | v0.36–v0.37 |
| WS-Q | Editable visual / page properties | ✅ shipped | v0.42–v0.43 |
| WS-O | Design alignment with AgentHub | 📋 proposed | TBD |

Legend: ✅ shipped • 🟡 partial • ⬜ not started • 📋 proposed (open questions)

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
- **B1. Empty Description on items.** Allow item creation / edit with an empty Description field (currently appears to require a value). Verify both create and update paths.
- **B2. Placeholder below the Fixer.** Stray placeholder element renders below the Fixer panel — delete it.
- **B3. Close button.** Test the close button in the Fixer view. If it does not actually close the surface (or behaves inconsistently with other AgentHub items), wire it up. If the button is non-functional and not needed, remove it instead.
- **B4. AgentHub item persistence.** Re-verify persistence of the AgentHub item linked to the PBI Fixer — last check showed unstable state. Reproduce on reload + cross-session and confirm.

### AgentHub-shell — bugs (coordinate with WS-O / Lukasz)
- **B5. GitHub sign-in greys out the hub.** When signing in with GitHub on a "create item" task in Developer Hub, the whole hub greys out and only recovers after a full page refresh. Reproduce the flow (create item → sign into GitHub → verify hub stays interactive). Likely a missing post-auth message handler or modal-overlay state that doesn't get cleared on the OAuth popup return.
- **B6. SSO prompted per tab.** Each new tab re-prompts for SSO. Either make SSO **silent/transparent** for tabs after the first, or — preferably — **single sign-on once for the whole Developer Hub** and share the token across tabs (e.g. via shared MSAL cache, broadcast channel, or service-worker token broker). If sharing across tabs is not possible (e.g. cross-origin iframes with strict storage isolation), document the technical reason in [CHANGELOG.md](CHANGELOG.md) so we stop revisiting the question.

### Workload icon — replace the generic briefcase / "weird bag" — **WS-O-ICON (proposal)**

The Fabric host's *Additional authentication or authorization required* trust dialog (and the workload card / favicon / AgentHub item icon) currently renders the stock `briefcase.png` shipped with the workload SDK template — that's the small bag icon users keep asking about. Single source of truth:

- `Developer Hub/Frontend/Package/Product.json` — fields:
  - `favicon` → browser tab favicon
  - `icon.name` → top-level workload icon (the one shown in the trust dialog + workload chooser)
  - `homePage.newSection.customActions[*].icon.name` → home-page "New" tile
- `Developer Hub/Frontend/Package/AgentHubItem.json` — per-item icons (currently `execute.png`, separate concern)
- All assets live in `Developer Hub/Frontend/Package/assets/images/` (today: `briefcase.png`, `dial.png`, `execute.png`, `BannerMedium.png`, `learningMaterial.png`, `fabricUX.jpg`, `AgentHub1.png`, `AgentHub2.png`).

Swap is a one-file commit (drop a new PNG, point `Product.json` at it, rebuild the manifest). Rough proposals — **pick one, don't implement all**:

- **P1 — Hammer + sparkle.** Reads as "developer tool that fixes things" — direct nod to the PBI Fixer feature set. Cheap to draw in Fluent style.
- **P2 — Toolbox with a small spark / star.** Generalises better than a hammer (we're more than just fixers), still toolish.
- **P3 — Stylised "DH" monogram.** Brandable, future-proof, matches the "Developer Hub" name. Risk: looks generic without colour.
- **P4 — Robot / agent head with a wrench overlay.** Communicates the agent-driven nature of the hub. Risk: "AI robot" iconography is overused in 2026.
- **P5 — Power BI yellow square + plug icon.** Strong PBI association, but locks the brand to PBI even though the hub is broader (Fabric-wide).
- **P6 — Fluent "Wrench" or "Toolbox" glyph from the FluentUI 2 icon set, exported at 32 / 48 / 96 px PNG.** Zero design budget, instantly on-brand with the rest of Fabric. Likely the highest-ROI option — recommend this as default unless we have a designer cycle for P1–P5.

Acceptance for whichever option is picked: replace `briefcase.png` (or add a new file and re-point `Product.json`), confirm the trust dialog + Fabric favicon + home-page tile all show the new glyph after `docker compose --profile prod build frontend` + recreate.

### Move "About" out of PBI Fixer into the Developer Hub shell — **WS-L revised**
The current plan has WS-L scoped as a Fixer-only About page. New requirement: **About belongs to the whole Developer Hub**, not the Fixer.

- **Move location:** bottom-left of the AgentHub sidebar, **above "Support"** (matches `.sidenav-footer-item` slot in `styles.scss`).
- **Delete the About entry from the PBI Fixer nav** (`types/nav.ts` → drop `about` NavKey; `Others` count goes 11 → 10).
- **Content plan:**
  - Developer Hub authors: **Lukasz** (upstream maintainer) and **Alexander Korn** (PBI Fixer + extensions)
  - Credit **Michael Kovalsky** for [`semantic-link-labs`](https://github.com/microsoft/semantic-link-labs) (the engine underneath the Fixer)
  - Build/version info — pulled from `utils/version.ts` for the Fixer + a hub-level version
  - Links: GitHub repo, semantic-link-labs repo, IBCS website (since IBCS is a shipped feature), notebook origin (`PBI-Fixer/pbi_fixer/`)
  - Short license / acknowledgements blurb
- **Owns (revised):**
  - `Developer Hub/Frontend/src/components/agenthub/AboutPage.tsx` — **NEW** (hub-level, not Fixer-level)
  - `Developer Hub/Frontend/src/components/agenthub/AgentHubLayout.tsx` — add About to footer slot above Support
  - `Developer Hub/Frontend/src/components/PbiFixer/types/nav.ts` — remove `about` NavKey
  - `Developer Hub/Frontend/src/components/PbiFixer/components/pages/AboutPage.tsx` — **DELETE**
- **Acceptance:**
  - [ ] About link appears in Developer Hub left sidebar above Support
  - [ ] About page lists Lukasz + Alexander as authors, credits Michael Kovalsky for semantic-link-labs
  - [ ] PBI Fixer "Others" no longer shows About; tab count drops to 10
  - [ ] Hub version pulled from a single source of truth

### WS-O design alignment — **bumped priority** based on user feedback
Visual alignment between Fixer and AgentHub is now an active complaint, not a "nice to have". Focus the first WS-O slice on the cheapest wins:
- **Font color and font sizes** (explicit user request) — audit every `Text` / heading / body in the Fixer surfaces against the AgentHub tokens. Strip remaining hard-coded `fontFamily` and `color` overrides in `ModelExplorer.tsx`, `ReportExplorer.tsx`, page headers, and DataGrid header rows.
- Then proceed with the rest of WS-O Phase 1 (sidebar palette, active accent bar, topbar). See **Appendix B** for the full plan.

---

## Remaining fixers (WS-E backlog)

13 fixer handlers shipped in v0.41 (8 SM + 5 Report — see CHANGELOG WS-E). Priority for the rest:

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
| Fix Bar Chart Formatting | `report/_Fix_BarChart.py` | 299 | P1 |
| Fix Column Chart Formatting | `report/_Fix_ColumnChart.py` | 299 | P1 |
| Fix Visual Alignment | `_Fix_VisualAlignment.py` | ~200 | P1 (snap misaligned visuals) |
| Migrate Report-Level Measures | `report/_Fix_MigrateReportLevelMeasures.py` | ~150 | P2 |
| Upgrade PBIRLegacy → PBIR | `report/_Fix_UpgradeToPbir.py` | 100 | P2 (sempy-labs runtime) |
| Migrate Slicers → Slicerbar | `report/_Fix_MigrateSlicerToSlicerbar.py` | ~150 | P2 |
| Convert Column → Line | `_Fix_ColumnToLine.py` | 118 | P3 |

### Semantic Model fixers (not yet ported)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Add Measures from Columns | `_Add_MeasuresFromColumns.py` | ~150 | P1 |
| Add LastRefresh Table | `_Add_Table_LastRefresh.py` | ~80 | P1 |
| Avoid Adding 0 (in measures) | `_Fix_AvoidAdding0.py` | ~60 | P1 |
| Trim Object Names | `_Fix_TrimObjectNames.py` | 71 | P2 |
| Use DIVIDE Function | `_Fix_UseDivideFunction.py` | 59 | P2 |
| Mark Primary Keys | `_Fix_MarkPrimaryKeys.py` | 44 | P2 |
| Measure Descriptions | `_Fix_MeasureDescriptions.py` | 40 | P2 |
| Capitalize Object Names | `_Fix_CapitalizeObjectNames.py` | ~60 | P2 |
| Set Data Category | `_Fix_DataCategory.py` | ~80 | P2 |
| Date Column Format | `_Fix_DateColumnFormat.py` | ~50 | P2 |
| Default Data Source Version | `_Fix_DefaultDataSourceVersion.py` | ~50 | P2 |
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

**Already shipped in v0.41** (for reference, do not re-port):
- SM: `Fix_FloatingPointDataType`, `Fix_DoNotSummarize`, `Fix_DiscourageImplicitMeasures`, `Fix_IsAvailableInMdxFalse`, `Fix_MeasureFormat`, `Fix_PercentageFormat`, `Fix_WholeNumberFormat`, `Fix_HideForeignKeys`
- Report: `Fix_PieChart`, `Fix_PageSize`, `Fix_HideVisualFilters`, `Fix_DisableShowItemsNoData`, `Fix_RemoveUnusedCustomVisuals`

**Priority legend:**
- **P0** — IBCS batch (signature feature, ship as one workstream)
- **P1** — high user value, ship next batch
- **P2** — medium value, ship after P1 batch
- **P3** — niche / formatting polish, ship opportunistically

---

## Explorer / Tab gaps (parity backlog)

Lower-priority parity items still missing from the existing TS components:

### Model Explorer
- DAX formatting (Python uses TOM `format_dax_expression`)
- Table data preview (TOPN DAX query + render)
- Editable properties (XMLA/TOM write-back)
- Multi-model support (types defined, logic stubbed)
- Perspective filtering (filter tree by perspective)
- Hierarchy levels rendering in tree (types defined)
- Partition details in properties (types defined)
- Right-click context menu actions
- Scan mode (BPA badges on tree items)

### Report Explorer
- ~~Live report preview / thumbnail~~ — **shipped.** Live, interactive Power BI report embed (not a thumbnail) via short-lived embed token minted server-side; `Load Report` / post-Save bumps a refresh key for fresh embed. See `ReportPreview` in `ReportExplorer.tsx` + `getReportEmbedToken` in `services/fabricApi.ts`.
- ~~Editable properties save-back~~ — **shipped (WS-Q v0.42).** `handleSaveProps` calls `updateVisualProperties` (visual + page) → backend `/pbi-fixer/visual/update` → Fabric REST `updateDefinition` LRO. Preview refreshes on save (WS-Q v0.43).
- Per-visual quick-fix buttons in properties panel
- Scan mode badges (counts shown, scan execution missing)
- Visual config JSON preview
- Drag-and-drop page reorder

### Other tabs not yet planned as standalone WSes
None — all originally-identified tabs are covered: Fixer (WS-E), Perspectives (WS-F), Translations (WS-G), Model BPA (WS-C), Report BPA (WS-D), Memory/Vertipaq (WS-B), Delta (WS-I), Prototype (WS-M), Diagram (WS-J), Script Runner (WS-K). About moved out to the AgentHub shell (WS-L revised). Visual Properties editor shipped as WS-Q.

---

## Open workstreams

### WS-K — Script Runner page
Monaco editor + Run button → backend eval endpoint. **Full-power**, feature-flagged on `PBI_FIXER_ENABLE_SCRIPT_RUNNER`. See **Appendix A** for security rationale.

**Owns:**
- `components/pages/ScriptRunnerPage.tsx`
- `Backend/agenthub/controllers/script_runner_controller.py`

**Acceptance:**
- [ ] Python REPL runs with `sempy`, `sempy_labs`, forwarded OBO tokens
- [ ] TS runner option (sandboxed worker) for quick DAX queries
- [ ] Streaming output (SSE or chunked)
- [ ] Red banner: "⚠ Full-power execution — runs as you, with your tokens. Local dev only."
- [ ] Disabled in UI + backend when env flag not `true`

**Dependencies:** WS-A only.

---

### WS-L — About page (revised — Developer Hub shell, not Fixer)
Moved out of the PBI Fixer into the AgentHub shell footer (above Support). See **Open bugs / UX tasks → "Move About out of PBI Fixer"** above for full scope, content plan, owns, and acceptance criteria.

**Dependencies:** Coordinate with the AgentHub shell maintainer (Lukasz) — touches `AgentHubLayout.tsx` + `styles.scss` footer slot.

---

### WS-N — Integration sweep (remaining work)
- [ ] Install Monaco shared deps for WS-K (when WS-K starts)
- [ ] Final smoke pass after each integration batch

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

# Appendix A — Script Runner (WS-K) Decision & Security Note

**Decision (Alexander, April 23 2026):** include Script Runner as **full-power** — Monaco editor in frontend, Python REPL in backend, access to `sempy`, `sempy_labs`, workspace Fabric/PBI tokens, container filesystem. No sandbox.

**What is Script Runner?** Tab with code editor + Run button. User writes arbitrary Python (or TS) and executes against currently-selected workspace/model/report. Typical use: one-off TOM tweaks, ad-hoc DAX, testing a fixer before wrapping into the Fixer page, debugging customer models in-session.

**Why in PLAN.md not README.md:** `Developer Hub/README.md` is shared with upstream maintainer (Lukasz). Script Runner is a power-user, security-sensitive backdoor → keep internal. Add a short note in `Developer Hub/README.md` pointing maintainers here. Do not ship enabled by default; gate on env var `PBI_FIXER_ENABLE_SCRIPT_RUNNER=true` in `docker-compose.yaml`.

**Security acknowledgements:**
- Code runs with backend service identity + forwarded user OBO tokens → full Fabric/PBI/OneLake access as the user.
- Arbitrary filesystem, network, subprocess access inside the container.
- No I/O sanitization — scripts can print tokens if asked.
- Acceptable **only** because Developer Hub runs locally against the user's own tenant. Never enable in shared/hosted deployments.

---

# Appendix B — WS-O — Design Alignment with AgentHub (proposed)

> Goal: make PBI Fixer shell visually and behaviourally feel like a first-class citizen of Developer Hub / AgentHub. Today the surfaces share host page chrome but diverge inside the `pbifixer` iframe — different sidebar palette, nav styling, active-state language, topbar pattern, no shared transitions.
>
> **Planning-only.** Open questions for Alexander listed at the end before WS-O kicks off.

## Side-by-side comparison (current build)

### Sidebar / left navigation
| Aspect | AgentHub (`AgentHubLayout.tsx` + `styles.scss`) | PBI Fixer (`PbiFixerNav.tsx` Fluent `makeStyles`) |
|---|---|---|
| Width | 224 px, animated collapse (200 ms cubic-bezier 0.33,0,0.67,1) | 220 px, no collapse, no animation |
| Background | `#f4f3f2` (warm grey) | `tokens.colorNeutralBackground2` (cooler) |
| Border-right | None — background contrast separates | 1 px `colorNeutralStroke2` divider |
| Item shape | `border-radius: 8px`, `margin: 2px 10px`, padding `8px 12px` | `borderRadiusMedium` (4 px), `padding: 6px 10px` |
| Hover | `rgba(233, 232, 231, 0.6)`, icon recolours | `colorNeutralBackground3Hover`, no icon recolour |
| Active | White card + 3 px primary accent bar + box-shadow + icon turns primary blue + animated slide-in | Subtle tint, semibold text, no accent bar, no animation |
| Icons | FluentUI 24 px regular icons | FluentUI 20 px regular icons (matches AgentHub family, smaller size) |
| Section dividers | `.sidenav-section-label` uppercase 11 px, `.sidenav-rail-divider` between groups | Single uppercase "PBI Fixer" header |
| Footer | `.sidenav-footer-item` slot | None |
| Keyboard | Tab + Enter/Space; arrow-key tree nav not present | Same; no arrow-key nav |

### Topbar / header
| Aspect | AgentHub `.agenthub-topbar` | PBI Fixer `styles.header` |
|---|---|---|
| Height | 48 px fixed | Implicit (~36 px), padding-driven |
| Background | `#faf9f8` warm | `colorNeutralBackground1` (white) |
| Layout | left = brand + search; right = icon cluster + avatar | left = title + version; nothing right |
| Search | Global search box with scope chips | None |
| Right cluster | 24 px icons (Help, Notifications, Chat) + 28 px avatar | None |

### Connection bar (PBI Fixer only)
- AgentHub has no equivalent. PBI Fixer puts a 3-combobox bar (Workspace / SM / Report) directly under header, full-width.
- Visual mismatch: bar uses same bg as content → topbar / connection bar / content look like one continuous slab.

### Page content surface
| Aspect | AgentHub | PBI Fixer |
|---|---|---|
| Padding | Mostly `24px` outer with cards | `12px 16px` (denser) |
| Card pattern | Soft cards `border-radius: 12px`, subtle shadow | Mostly flat panels, dense Fluent DataGrids |
| Empty/loading | Branded states with illustration + CTA | Generic Fluent `Spinner` + grey text |
| Font | Inherited Segoe UI | Inherited **except** ModelExplorer / ReportExplorer tree rows + DAX preview (custom font stack leaks) |

### Motion / micro-interactions
- AgentHub: consistent 120–200 ms cubic-bezier(0.33, 0, 0.67, 1) — sidebar collapse, accent bar slide-in, hover.
- PBI Fixer: **no transitions** anywhere — instant snaps.

### Tech stack split
- AgentHub: SCSS classes (`styles.scss`, BEM-ish). PBI Fixer: FluentUI `makeStyles` with `tokens.*`. Zero overlap.
- Result: theming changes in `styles.scss` do **not** reach PBI Fixer. Surfaces diverge with every Lukasz redesign unless we share classes or re-derive from tokens.

---

## Proposed alignment plan

### Phase 1 — Visual parity (no behavioural change)
1. Switch PBI Fixer sidebar to AgentHub class language. Replace `makeStyles` rules with shared `.agenthub-sidenav`, `.sidenav-item`, `.sidenav-item--active`, `.sidenav-section-label`, `.sidenav-rail-divider`. Keeps markup React-controlled, pulls styling from shared SCSS → automatic theming parity.
2. ~~Replace emoji glyphs with FluentUI icons.~~ **Done in v1.2** — nav uses FluentUI 20 px regular icons (`Database20Regular`, `ChartMultiple20Regular`, `Wrench20Regular`, `DatabaseSearch20Regular`, `DocumentSearch20Regular`, `Storage20Regular`, `Eye20Regular`, `Translate20Regular`, `ArrowSwap20Regular`, `Flowchart20Regular`, `Code20Regular`, `Beaker20Regular`, `ArrowImport20Regular`, `Flash20Regular`, `Info20Regular`). Optional follow-up: bump to 24 px to match AgentHub size exactly.
3. Promote active-page accent bar (3 px primary + white card + icon recolour matching `.sidenav-item--active`).
4. Add slide-in accent animation (`@keyframes sidenavAccentIn` — already in SCSS).
5. Topbar redesign: move title + version into `.agenthub-topbar`-styled strip. Right-side cluster: Help link to GitHub README, notifications dot for fixer-run results (placeholder), build/version pill. 48 px height + warm `#faf9f8`.
6. Reframe connection bar — two options (see open questions): (a) restyled horizontal strip under topbar with warm surface palette + 1 px hairline → reads as topbar sub-header; (b) move pickers into sidebar above nav (Workspace / SM / Report stacked), content area gets full width.
7. Page content padding: bump from `12px 16px` to `24px`. Audit each page for double-padding regressions.
8. Font normalisation: strip leftover hard-coded `fontFamily` in `ModelExplorer.tsx` / `ReportExplorer.tsx` tree rows + filter input + properties panels.
9. Shared empty/loading states: replace bare `Spinner` + grey text with reusable `<EmptyState />` styled like AgentHub.

### Phase 2 — Motion + interaction parity
1. Adopt AgentHub motion curve for hover, active, Others expand/collapse.
2. Soft fade/slide on page swap (~120 ms opacity crossfade, no horizontal motion → avoid disorienting on dense grids).
3. Arrow-key tree navigation (↑/↓ select, →/← expand/collapse Others, Enter activate).

### Phase 3 — Optional behavioural lifts (decide per question)
1. Make PBI Fixer pages **lazy-loaded chunks** with `preload()`-on-hover, mirroring `AgentHubLayout` `lazyWithPreload`.
2. Hook PBI Fixer into `EditorTabsProvider` / `EditorGroupsRoot` so Fixer pages open in tabs alongside AgentHub pages. Big payoff, big refactor — needs Lukasz alignment.
3. Right-click context menu on Fixer nav items reusing `SideNavContextMenu`.
4. Route PBI Fixer nav state through React Router (instead of `sessionStorage`) for deep-links.

### Out of scope for WS-O
- Search bar in Fixer topbar (no PBI-Fixer search index).
- Avatar / sign-out in Fixer topbar (host AgentHub topbar already has it).
- Replacing FluentUI v9 components with custom SCSS (only restyle chrome).

### Acceptance (proposed)
- [ ] Sidebar visually indistinguishable from AgentHub at first glance
- [ ] Topbar matches AgentHub 48 px warm-surface pattern
- [ ] Connection bar restyled (option a or b per open question), no third visual stripe
- [ ] All transitions use shared cubic-bezier curve
- [ ] No remaining hard-coded `fontFamily` in tree rows / properties panels
- [ ] Light + dark mode both render parity with AgentHub home

### Owns (proposed)
- `components/PbiFixerNav.tsx` — rewrite styling to use shared SCSS classes
- `components/PbiFixerPage.tsx` — header + connection-bar restyle, content padding
- `types/nav.tsx` — already uses `React.ReactNode` icons (v1.2). No further work.
- `styles.scss` — only **append** new classes if needed (e.g. `.pbifixer-connection-bar`); do not mutate existing AgentHub classes
- `components/ModelExplorer.tsx`, `components/ReportExplorer.tsx` — font normalisation + empty-state replacement
- New `components/common/EmptyState.tsx`

---

## WS-O decisions (locked, May 2026)

All open questions answered by Alexander. WS-O scope below; older question/recommendation text removed (kept in git history).

| # | Decision | Notes |
|---|---|---|
| 1 | **Connection bar — restyle in place (option a)** | Keep position, change bg to AgentHub warm `#faf9f8` + 1 px hairline so it reads as topbar sub-header. |
| 2 | **Migrate Fixer chrome to shared SCSS classes** | Refactor `PbiFixerNav.tsx` + `PbiFixerPage.tsx` to `className` against `.agenthub-sidenav`, `.sidenav-item`, `.sidenav-item--active`, etc. Drop the corresponding `makeStyles` rules. |
| 3 | **Topbar right cluster — skip entirely** | No Help / version / Open-in-Fabric. Host AgentHub topbar covers the right side. |
| 4 | **Lazy-loading — defer** | Out of WS-O. Park for a dedicated perf pass when bundle size becomes a complaint. |
| 5 | **EditorGroups / tabs integration — out of scope for WS-O** | Park as a separate workstream after WS-O lands. Needs Lukasz alignment before sizing. |
| 6 | **Switch nav to React Router** | URL-driven Fixer nav state; deep-links like `#/agent-hub/pbifixer/diagram` work. Browser back/forward navigates between pages. Drop `sessionStorage["pbiFixer.activeNav"]` (keep `pbiFixer.othersExpanded` if still relevant). |
| 7 | **Sidebar footer — leave empty** | No "Report a bug", "Open notebook", or version pill in the Fixer sidebar bottom. |
| 8 | **AgentHub blue (`#005faa`) applies everywhere — including text** | Not just the accent bar. Audit every text color, link color, heading color, focus ring across the Fixer surfaces and align to the AgentHub blue palette. Replaces stray `colorBrandForeground*` / `tokens.colorBrand*` overrides where they diverge from `#005faa`. |
| 9 | **Page-swap motion — crossfade matching AgentHub** | ~120 ms opacity crossfade on `activeNav` change. No horizontal slide. |
| 10 | **Hide nav items with `ready: false`** | Matches AgentHub (never advertises unfinished pages). `NAV_ITEMS` filter at render time. CHANGELOG/PLAN remain the roadmap source for stakeholders. |

### Implications for WS-O Owns / Acceptance

Update the WS-O Owns + Acceptance lists above to reflect these decisions:
- **Owns adds:** routing wiring (likely a small `PbiFixerRouter.tsx` mounting React Router routes for each `NavKey`).
- **Owns drops:** topbar right cluster components, Fixer-sidebar footer components.
- **Acceptance adds:**
  - [ ] Deep-link `#/agent-hub/pbifixer/<navKey>` routes correctly on first load
  - [ ] Browser back/forward navigates between Fixer pages
  - [ ] All visible text uses AgentHub blue palette (no rogue brand colors leaking) — partial: hard-coded `#555` `propLabel` text in `ModelExplorer.tsx` + `ReportExplorer.tsx` swapped for `tokens.colorNeutralForeground2` (May 5 2026); full `#005faa` audit across status colors / link colors still pending
  - [x] Nav items with `ready: false` do not render in the sidebar — `NAV_ITEMS` in `types/nav.tsx` now exports the filtered subset; full registry kept under `ALL_NAV_ITEMS_REGISTRY` so the `NavKey` union and any deep-link logic still resolve `scriptRunner` (May 5 2026)
- **Acceptance drops:**
  - Topbar right cluster, sidebar footer, dark-mode parity, lazy-loading, EditorGroups integration.

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green light from Alexander
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param reserved, no UI)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
