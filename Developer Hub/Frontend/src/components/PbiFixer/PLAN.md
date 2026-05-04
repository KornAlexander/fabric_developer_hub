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

| Fixer | Python File | Lines | Notes |
|---|---|---|---|
| **Add Calculated Calendar Table** | `_Add_CalculatedTable_Calendar.py` | ~290 | DAX `CALENDARAUTO`, full date hierarchy + sort-by-column, **auto-detects fact-table date columns and creates Many→One relationships to `CalcCalendar[Date]`**. Backend bridge required (TOM `add_relationship`, calculated tables) |
| **Add Measure Table** | `_Add_CalculatedTable_MeasureTable.py` | ~150 | Empty calculated table for measure organization |
| **Add CalcGroup — Time Intelligence** | `_Add_CalcGroup_TimeIntelligence.py` | ~200 | YTD / MTD / QTD / PY / YoY calc-group items |
| **Add CalcGroup — Units** | `_Add_CalcGroup_Units.py` | ~150 | k / M / B unit scaling calc group |
| **Add PY Measures** | `_Add_PYMeasures.py` | ~150 | Generates `PY`, `Δ PY`, `Δ PY %`, `Max Green PY`, `Max Red AC` for every detected AC measure (prerequisite for IBCS Variance) |
| **Fix IBCS Variance** | `_Fix_IBCSVariance.py` | **771** | Transforms column charts → IBCS variance: ensures PY measures exist, adds them to visual, sets **error bars (red `#FF0000`)**, overlap, labels, AC/PY colors, axes, sorting. Largest single fixer in the suite |
| **Fix All Charts (IBCS pass)** | `_Fix_Charts.py` | ~200 | Unified IBCS-friendly cleanup across Bar/Column/Line: no gridlines, data labels on, clean axes |
| **Apply IBCS Theme** | (theme JSON in `_report_theme.py`) | ~100 | Push IBCS-compliant report theme |

**Priority: P0** — these are the headline features. Ship in this exact dependency order:
Calendar → Measure Table → CalcGroups → PY Measures → IBCS Variance → Fix Charts → Theme.
All require the **TMDL/PBIR round-trip backend pattern** (already shipped in v0.40/v0.41 for
translations + simple SM fixers); the additional unknown is whether the TMDL writer can emit
**calculated tables**, **calculation groups**, and **relationships** (current `tmdl_translations.py`
only mutates cultures). Spike that question first before sizing the batch.

**Backend bridge gap:** TOM-level operations (`tom.add_relationship`, calc-group creation,
calculated-table creation) may need a real sempy-labs runtime in the backend container —
TMDL emission alone could be enough for tables + relationships, but calc groups need
`calculationGroup` blocks in TMDL which `tmdl_translations.py` doesn't model yet. **Decide
spike vs. real sempy-labs install before starting.**

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
- Live report preview / thumbnail (Python uses `exportToFile` API)
- Editable properties save-back (pending state exists, no write)
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

## Open questions for Alexander (answer in chat)

Resolved already (do not re-ask):
- ~~Icon set~~ — FluentUI 20 px icons shipped in v1.2 (`Database20Regular`, `ChartMultiple20Regular`, …). Optional follow-up: bump to 24 px to match AgentHub size exactly.
- ~~Dark mode~~ — **not a priority.** Token-only path is fine; no explicit dark-mode pass needed.

Remaining decisions, with context + a recommendation for each:

### Q1. Connection bar treatment
Today: a horizontal strip with three Comboboxes (Workspace / Semantic Model / Report) sits directly under the header, full-width across the body. AgentHub has no equivalent — its pages have no "connection context" concept, so we have to invent one that *feels* AgentHub-native.

- **Option (a) — restyled horizontal strip:** keep position, change background to AgentHub warm surface `#faf9f8`, add a soft 1 px hairline so it visually reads as a sub-header of the topbar instead of a third stripe. Lowest disruption to muscle memory.
- **Option (b) — move into sidebar above nav:** stack Workspace / SM / Report vertically as the top section of the sidebar. Frees full horizontal width for the content area, exactly mirrors no-connection-bar AgentHub pages. Loses ~250 px of sidebar height; pickers harder to scan; muscle memory breaks for current users.

**Recommendation: (a).** Cheap visual fix, no UX regression.

### Q2. SCSS classes vs FluentUI `makeStyles` for the chrome
PBI Fixer chrome currently uses `makeStyles` with `tokens.*`. AgentHub chrome uses SCSS classes in `styles.scss` (`.agenthub-sidenav`, `.sidenav-item`, etc.).

- **Migrate to shared SCSS classes:** automatic visual parity — every redesign Lukasz ships in `styles.scss` reaches the Fixer for free. Cost: one-time refactor of `PbiFixerNav.tsx` + `PbiFixerPage.tsx` to use `className` instead of `makeStyles`.
- **Stay on `makeStyles` + tokens:** Fixer stays self-contained; no shared-file ownership conflict with Lukasz; but every Lukasz redesign forces a manual re-derive in our token map.

**Recommendation: migrate to shared SCSS classes.** One-time cost, prevents permanent drift. Fixer chrome should not own its own visual language.

### Q3. Topbar right cluster
AgentHub topbar has icons (Help, Notifications, Chat) + avatar on the right. PBI Fixer topbar today is empty on the right. The host AgentHub topbar already shows the avatar one tier up, so we don't need to duplicate that.

Proposed minimum content:
- Help link → GitHub README of `pbi_fixer`
- Version pill (`v0.x` from `utils/version.ts`)
- "Open in Fabric" link (deep-links the currently-selected workspace/SM/report into Fabric)

**Recommendation: ship those three.** Skip notifications + avatar (host hub already provides them). Anything else you want there?

### Q4. Lazy-loading PBI Fixer pages
AgentHub pages each have their own webpack chunk + `preload()` on hover. PBI Fixer today bundles all 15 pages in a single chunk.

- **Lazy-load now (in WS-O):** mirrors AgentHub pattern, ~30–50 % smaller initial bundle, faster first paint. Adds Suspense boundaries you have to handle (loading spinners, error boundaries).
- **Defer to a later perf pass:** Fixer bundle isn't huge today; the win is modest.

**Recommendation: defer.** WS-O is already big; keep this for a dedicated perf pass when bundle size becomes a real complaint.

### Q5. Tabs / EditorGroups integration
AgentHub uses `EditorTabsProvider` + `EditorGroupsRoot` so users can open multiple pages as tabs, split the view, drag-reorder. PBI Fixer today has only one active page at a time.

- **Integrate:** most AgentHub-native possible — a Fixer page would behave exactly like any other AgentHub item. Big payoff for power users.
- **Cost:** requires touching `AgentHubLayout.tsx` (Lukasz's territory), agreeing on a contract for how a Fixer page registers as a tab, chunky refactor of `PbiFixerPage.tsx` (it can no longer assume it owns the whole content area).

**Recommendation: out of scope for WS-O.** Park as a separate workstream after WS-O lands. Needs Lukasz alignment before sizing.

### Q6. Routing — React Router vs sessionStorage
Today the active page is stored in `sessionStorage` (`pbiFixer.activeNav`). URLs do not change. You can't deep-link to `#/agent-hub/pbifixer/diagram` from a doc or chat.

- **Switch to React Router:** deep-links work, browser back/forward navigates between Fixer pages, easier to share specific tabs.
- **Stay on sessionStorage:** simpler, Fixer remains a sub-app rather than a route-aware citizen.

**Recommendation: switch to React Router.** Cheap win, big UX dividend (paste a deep-link to a Diagram of a specific dataset in an email and it just works).

### Q7. Footer slot in the Fixer sidebar
AgentHub sidebar has a footer slot (`.sidenav-footer-item`) for items like Help / Sign-out / Settings. The Fixer sidebar today has nothing at the bottom.

If About moves to the AgentHub shell footer (already decided in Open bugs / UX tasks → WS-L revised), the Fixer sidebar footer can host:
- "Report a bug" → GitHub Issues
- "Open notebook version" → link to the Python notebook source
- Fixer version pill

**Recommendation: yes, add a footer slot** with those three. Mirrors AgentHub's pattern.

### Q8. Active-state accent colour
AgentHub uses `#005faa` (primary blue) for the active-page accent bar.

- **Reuse `#005faa`:** maximum seamlessness — the Fixer disappears into AgentHub's visual language. Best for users who use multiple AgentHub items.
- **Pick a distinct PBI accent (e.g. PBI yellow `#F2C811`):** users *see* they're inside the Fixer surface. Helps onboarding and screenshots.

**Recommendation: reuse `#005faa`.** Topbar title "PBI Fixer" already provides the identity cue.

### Q9. Page-swap motion
When the active page changes (e.g. you click Diagram → Memory):

- **Opacity crossfade (~120 ms):** matches AgentHub's motion language. Smoother on the eye.
- **Instant swap:** no transition. Slightly faster; some users find motion annoying on dense data grids.

**Recommendation: opacity crossfade.** 120 ms is fast enough not to feel laggy.

### Q10. Disabled "Coming soon" items in the nav
Today the Fixer shows nav items with `ready: false` as italic + muted (currently: Script Runner, About). AgentHub never lists unfinished pages — they're hidden until ready.

- **Keep showing (current behaviour):** users see the roadmap. Helps when demoing upcoming features.
- **Hide until ready:** matches AgentHub. Cleaner nav.

**Recommendation: hide until ready.** Matches AgentHub's pattern. CHANGELOG / PLAN already documents the roadmap for stakeholders.

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green light from Alexander
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param reserved, no UI)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
