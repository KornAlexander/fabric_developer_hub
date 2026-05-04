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
| WS-L | About | ⬜ not started | — |
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

## Remaining fixers (WS-E backlog)

13 fixer handlers shipped in v0.41 (8 SM + 5 Report — see CHANGELOG WS-E). Priority for the rest:

### Report fixers (not yet ported)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Fix Bar Chart Formatting | `report/_Fix_BarChart.py` | 299 | P1 |
| Fix Column Chart Formatting | `report/_Fix_ColumnChart.py` | 299 | P1 |
| Upgrade PBIRLegacy → PBIR | `report/_Fix_UpgradeToPbir.py` | 100 | P2 (sempy-labs) |
| Migrate Slicers → Slicerbar | `report/_Fix_MigrateSlicerToSlicerbar.py` | ~150 | P2 |
| Fix Line Chart | `report/_Fix_LineChart.py` | ~200 | P2 |
| Fix KPI Card | `report/_Fix_KpiCard.py` | ~150 | P2 |
| Fix Matrix | `report/_Fix_Matrix.py` | ~200 | P2 |
| Fix Table | `report/_Fix_Table.py` | ~200 | P2 |
| Fix Titles | `report/_Fix_FixTitles.py` | ~100 | P2 |
| Set Data Source Version | `report/_Fix_SetDataSourceVersion.py` | 84 | P3 |
| Convert Column → Line | `_Fix_ColumnToLine.py` | 118 | P3 |

### Semantic Model fixers (not yet ported)

| Fixer | Python File | Lines | Priority |
|---|---|---|---|
| Add Measures from Columns | `semantic_model/_Add_MeasuresFromColumns.py` | ~150 | P1 |
| Add PY Measures | `semantic_model/_Add_PYMeasures.py` | ~150 | P1 |
| Trim Object Names | `semantic_model/_Fix_TrimObjectNames.py` | 71 | P2 |
| Use DIVIDE Function | `semantic_model/_Fix_UseDivideFunction.py` | 59 | P2 |
| Mark Primary Keys | `semantic_model/_Fix_MarkPrimaryKeys.py` | 44 | P2 |
| Measure Descriptions | `semantic_model/_Fix_MeasureDescriptions.py` | 40 | P2 |
| Month Column Format | `semantic_model/_Fix_MonthColumnFormat.py` | 38 | P3 |
| Sort Month Column | `semantic_model/_Fix_SortMonthColumn.py` | 61 | P3 |
| Flag Column Format | `semantic_model/_Fix_FlagColumnFormat.py` | 48 | P3 |
| IsAvailableInMDX (True) | `semantic_model/_Fix_IsAvailableInMdxTrue.py` | 49 | P3 |

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
None — all 12 originally-identified tabs are covered: Fixer (WS-E), Perspectives (WS-F), Translations (WS-G), Model BPA (WS-C), Report BPA (WS-D), Memory/Vertipaq (WS-B), Delta (WS-I), Prototype (WS-M), Diagram (WS-J), Script Runner (WS-K), About (WS-L). Visual Properties editor shipped as WS-Q.

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

### WS-L — About page
Version, links (GitHub, sempy-labs, notebook demo), credits, build info.

**Owns:** `components/pages/AboutPage.tsx`

**Acceptance:**
- [ ] Shows current `v0.x` from `utils/version.ts`
- [ ] Links open in new tab

**Dependencies:** WS-A only. Trivial.

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
| Icons | FluentUI 24 px regular icons | Plain Unicode emoji glyphs (🗂 📊 ⚡ …), 16 px |
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
2. Replace emoji glyphs with FluentUI 24 px regular icons. Map each `NavKey` (e.g. `model → DatabaseStack24Regular`, `report → ChartMultiple24Regular`, `fixer → Wrench24Regular`, `modelBpa/reportBpa → BookSearch24Regular`, `memory → MemoryRegular24Regular`, `perspectives → Eye24Regular`, `translations → Globe24Regular`, `delta → ArrowSwap24Regular`, `diagram → Flowchart24Regular`, `scriptRunner → Code24Regular`, `prototype → Beaker24Regular`, `about → Info24Regular`). **Verify each icon exists in installed `@fluentui/react-icons` bundle** (see WS-J v0.20 gotcha — TS compile passes for non-existent names; runtime is `undefined`).
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
- `types/nav.ts` — replace `icon: string` (emoji) with `icon: React.ReactNode` (FluentUI icons); verify each name exists in bundle
- `styles.scss` — only **append** new classes if needed (e.g. `.pbifixer-connection-bar`); do not mutate existing AgentHub classes
- `components/ModelExplorer.tsx`, `components/ReportExplorer.tsx` — font normalisation + empty-state replacement
- New `components/common/EmptyState.tsx`

---

## Open questions for Alexander (answer before WS-O kicks off)

1. **Connection bar treatment** — (a) restyled horizontal strip under topbar, or (b) move pickers into sidebar above nav?
2. **Icon set** — replace playful emoji glyphs (🗂 📊 ⚡ 👁 🌐 …) with monochrome FluentUI 24 px icons?
3. **SCSS vs `makeStyles`** — migrate Fixer chrome to shared `styles.scss` classes, or stay token-only and re-derive look from `tokens.*`?
4. **Topbar right cluster** — what lives there? Suggested minimum: Help link, version pill, "Open in Fabric" link. Skip notifications + avatar (host hub already has)?
5. **Lazy-loading** — worth doing in WS-O, or defer to later perf pass?
6. **Tabs/EditorGroups integration** — should PBI Fixer pages participate in AgentHub `EditorGroupsRoot`? Most AgentHub-native possible but requires touching `AgentHubLayout` and Lukasz negotiation. In/out for WS-O?
7. **Routing** — switch from `sessionStorage` to React Router for deep-links?
8. **Footer slot** — match AgentHub `.sidenav-footer-item` (Report a bug, Open notebook version, version pill)?
9. **Active-state accent colour** — keep AgentHub's `#005faa` primary, or distinct PBI-Fixer accent (e.g. PBI yellow `#F2C811`)? Identity vs. seamlessness.
10. **Page-swap motion** — opacity crossfade OK, or instant swaps?
11. **Disabled "Coming soon" items** — keep showing (italic + muted), or hide until ready (matches AgentHub)?
12. **Dark mode** — confirm priority. SCSS reuse → "free"; token-only → explicit pass.

---

## Non-Goals (explicitly out of scope for v0.x)
- v1.0 — requires explicit green light from Alexander
- Custom BPA rule authoring (use sempy-labs default ruleset; `rulesetUrl?` param reserved, no UI)
- Offline mode
- Mobile layout
- Hosted/multi-tenant deployment hardening (Script Runner stays local-only)
