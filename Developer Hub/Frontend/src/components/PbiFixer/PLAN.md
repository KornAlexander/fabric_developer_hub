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
| WS-L | About — **moved to AgentHub shell** (was Fixer page) | ✅ shipped | (AgentHub footer) |
| WS-M | Prototype | ✅ shipped | v0.16 |
| WS-N | Integration sweep | 🟡 partial | v0.36–v0.37 |
| WS-Q | Editable visual / page properties | ✅ shipped | v0.42–v0.43 |
| WS-O | Design alignment with AgentHub | ✅ shipped | v0.55 |

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
- **B1. Empty Description on items.** Allow item creation / edit with an empty Description field. Backend / `ItemContext.createItem` already passes `description || ""`, so the empty path is supported in code — likely a UI validation in the create dialog. Verify in the actual create dialog (Fabric host or in-workload).
- **B2. Placeholder below the Fixer.** Stray placeholder element renders below the Fixer panel — delete it.
- **B3. Close button.** Test the close button in the Fixer view. If it does not actually close the surface (or behaves inconsistently with other AgentHub items), wire it up. If the button is non-functional and not needed, remove it instead.
- ~~**B4. AgentHub item persistence.**~~ — **closed.** Persistence shipped via WL-1 (manifest → `/agenthub-item-editor/:itemObjectId` route; `AGENTHUB_DATA_DIR` bind-mounted to `./Backend/.data`; `schema_version` field on `AgentHubMetadata`). See [Developer Hub PLAN.md WL-1 C1–C3, C8](../../../../PLAN.md#wl-1--research-findings--gap-analysis-april-24-2026). Final manual + Playwright validation tracked there as C4 / C5.

### AgentHub-shell — bugs (coordinate with WS-O / Lukasz)
- **B5. GitHub sign-in greys out the hub.** When signing in with GitHub on a "create item" task in Developer Hub, the whole hub greys out and only recovers after a full page refresh. Reproduce the flow (create item → sign into GitHub → verify hub stays interactive). Likely a missing post-auth message handler or modal-overlay state that doesn't get cleared on the OAuth popup return.
- **B6. SSO prompted per tab.** Each new tab re-prompts for SSO. Either make SSO **silent/transparent** for tabs after the first, or — preferably — **single sign-on once for the whole Developer Hub** and share the token across tabs (e.g. via shared MSAL cache, broadcast channel, or service-worker token broker). If sharing across tabs is not possible (e.g. cross-origin iframes with strict storage isolation), document the technical reason in [CHANGELOG.md](CHANGELOG.md) so we stop revisiting the question.

### Workload icon — replace the generic briefcase / "weird bag" — **WS-O-ICON** ✅ shipped

Custom `developerHub.png` ships at [`Frontend/Package/assets/images/developerHub.png`](../../../../Package/assets/images/developerHub.png) and is wired into `Product.json` for both `favicon` and `icon.name` (top-level workload icon shown in the trust dialog + workload chooser + "New" tile). Stock `briefcase.png` removed from the assets folder.

Verify after next `docker compose --profile prod build frontend`: trust dialog, Fabric favicon, and home-page tile all render the new glyph.

### Move "About" out of PBI Fixer into the Developer Hub shell — **WS-L revised** ✅ shipped

**Status:** Done. About lives in the AgentHub shell footer above Support; Fixer-level page deleted; `about` NavKey removed from `types/nav.tsx`.

Files in place:
- [`Frontend/src/components/AgentHub/AboutPage.tsx`](../../AgentHub/AboutPage.tsx)
- [`Frontend/src/components/AgentHub/AgentHubLayout.tsx`](../../AgentHub/AgentHubLayout.tsx) — `sidenav-footer-item` opens About via `openTab({ id: "about", kind: "about", … })`
- `Frontend/src/components/PbiFixer/components/pages/AboutPage.tsx` — deleted

Follow-up (low-prio): confirm About content lists Lukasz + Alexander as authors and credits Michael Kovalsky for `semantic-link-labs`; confirm a single source-of-truth hub version (workload version constant lives in topbar — see WL-1 C8).

### WS-O design alignment — ✅ shipped (v0.52–v0.55)

All 10 locked WS-O decisions landed across v0.52–v0.55. See [CHANGELOG.md](CHANGELOG.md). Remaining low-prio polish: full `#005faa` audit across status / link colors (decision #8), and the Phase 1 "font color + sizes" sweep can be reopened opportunistically if any divergence is reported.

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

Lower-priority parity items still missing from the existing TS components:

### Model Explorer
- DAX formatting (Python uses TOM `format_dax_expression`)
- Table data preview (TOPN DAX query + render)
- ~~Editable properties (XMLA/TOM write-back)~~ — **measure write-back shipped.** `updateMeasureProperties` in [`services/fabricApi.ts`](services/fabricApi.ts) patches `expression` / `formatString` / `description` / `displayFolder` / `isHidden` per-measure via TMDL round-trip; wired into `ModelExplorer.tsx` `handleSaveEdits`. Column / table / relationship property edits still TODO.
- Multi-model support (types defined, logic stubbed)
- Perspective filtering (filter tree by perspective)
- Hierarchy levels rendering in tree (hierarchies parsed in `fabricApi.ts`, levels not yet emitted into the tree)
- Partition details in properties (count shown, properties not expanded)
- Right-click context menu actions
- Scan mode (BPA badges on tree items)

### Report Explorer
- Per-visual quick-fix buttons in properties panel
- Scan mode badges (counts shown, scan execution missing — `scanResults` is an empty `useState`)
- Visual config JSON preview
- Drag-and-drop page reorder

> Shipped items moved out of this backlog: live Power BI embed (WS-Q v0.43), visual + page property save-back (WS-Q v0.42 — `updateVisualProperties` → `/pbi-fixer/visual/update`), measure property save-back (TMDL `updateMeasureProperties`). See [CHANGELOG.md](CHANGELOG.md).

### Other tabs not yet planned as standalone WSes
None — all originally-identified tabs are covered: Fixer (WS-E), Perspectives (WS-F), Translations (WS-G), Model BPA (WS-C), Report BPA (WS-D), Memory/Vertipaq (WS-B), Delta (WS-I), Prototype (WS-M), Diagram (WS-J). About moved out to the AgentHub shell (WS-L revised). Visual Properties editor shipped as WS-Q. **Script Runner (former WS-K) explicitly removed May 2026** — see Non-Goals.

---

## Open workstreams

### WS-L — About page (revised — Developer Hub shell, not Fixer) — ✅ shipped
About lives in the AgentHub shell footer above Support (`AboutPage.tsx` + `AgentHubLayout.tsx` `sidenav-footer-item`). Fixer-level page deleted, `about` NavKey removed from `types/nav.tsx`. Content polish (authors / credits / single-source hub version) tracked as low-prio follow-up in the section above.

---

### WS-N — Integration sweep (remaining work)
- [ ] Final smoke pass after each integration batch
- [x] LLM-backed translation propose endpoint (replaces glossary stub) — `Backend/src/api/agenthub_controller.py` (`_llm_translate_batch` + `pbi_fixer_translations_propose`); user-supplied `glossary` forwarded as preferred terminology, batched single Copilot call per culture, JSON response, fallback to source on failure. Frontend payload shape unchanged.

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

# Appendix A — Script Runner (WS-K) — ❌ removed May 2026

Script Runner was scoped as a full-power Monaco editor + backend Python REPL with forwarded OBO tokens. Removed at user request — not a fan, security surface not worth the maintenance. Frontend stub, `scriptRunner` NavKey + nav row, `PbiFixerPage` switch case, and `Code20Regular` icon import all deleted in v0.56. No backend code was ever shipped. Listed under Non-Goals so it does not get re-proposed.

---

# Appendix B — WS-O — Design Alignment with AgentHub (✅ shipped v0.52–v0.55)

> Historical reference. Original side-by-side analysis, phase plan, and the 10 locked decisions are preserved in git history; the implementation summary now lives in [CHANGELOG.md](CHANGELOG.md) under v0.52–v0.55.
>
> Outstanding low-prio polish (audited May 2026): full `#005faa` audit across status / link colors (decision #8 — `#555` `propLabel` swapped to `tokens.colorNeutralForeground2`; brand-color audit across the rest still pending).

## Locked decisions (kept for reference)

The 10 decisions below were locked in May 2026 and shipped across v0.52–v0.55. Side-by-side comparison, phase plan, and per-decision implications removed (kept in git history).

| # | Decision | Notes |
|---|---|---|
| 1 | Connection bar — restyle in place | AgentHub warm `#faf9f8` + 1 px hairline so it reads as topbar sub-header. |
| 2 | Migrate Fixer chrome to shared SCSS classes | `PbiFixerNav.tsx` + `PbiFixerPage.tsx` now use `pbifixer-subnav-item` / shared sidenav classes. |
| 3 | Topbar right cluster — skip entirely | Host AgentHub topbar covers the right side. |
| 4 | Lazy-loading — deferred | Park for a dedicated perf pass when bundle size becomes a complaint. |
| 5 | EditorGroups / tabs integration — separate workstream | Needs Lukasz alignment before sizing. |
| 6 | URL-driven nav, drop sessionStorage | `?nav=` query is the source of truth; `popstate` listener wires browser back/forward. `pbiFixer.expandedGroups` sessionStorage entry intentionally kept. |
| 7 | Sidebar footer — leave empty | No "Report a bug" / version pill in the Fixer sidebar bottom. |
| 8 | AgentHub blue (`#005faa`) everywhere — including text | Partial: `#555` `propLabel` swapped to `tokens.colorNeutralForeground2`; full audit across status / link colors still pending. |
| 9 | Page-swap motion — crossfade matching AgentHub | ~120 ms opacity crossfade on nav change. |
| 10 | Hide nav items with `ready: false` | `NAV_ITEMS` in `types/nav.tsx` exports the filtered subset; `ALL_NAV_ITEMS_REGISTRY` keeps the full registry for `NavKey` resolution. |

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green light from Alexander
- **Script Runner / arbitrary code execution** — removed May 2026 (was WS-K). Don't propose again.
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param reserved, no UI)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
