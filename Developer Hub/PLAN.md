# Developer Hub — Workload-Level Plan

> Workload-wide roadmap items that sit **outside** the PBI Fixer scope.
> For PBI-Fixer-specific workstreams see
> [`Frontend/src/components/PbiFixer/PLAN.md`](Frontend/src/components/PbiFixer/PLAN.md).

---

## WL-1 — Persistent Workload Item in Fabric Workspace

> **Status (May 5 2026):** ~85% shipped. C1 (route fix), C2 (durable bind-mount), C3 (`schema_version`), C8 (workload version badge) all merged. **Remaining: C4 (manual end-to-end Fabric portal walk) and C5 (Playwright regression).** C6 (Fixer state persistence) and C7 (per-item ACL) deferred to WL-1.1 / WL-1.2.

**Goal:** Make the Developer Hub item **persistently created** in the Fabric workspace where
the user launches it (today the item is transient / session-scoped). Once created it must
survive browser reloads, tenant re-logins, and show up in the workspace item list with its
own icon + rename + delete + permissions semantics, just like a Notebook or Lakehouse.

**Context:** Fabric custom workloads have a well-defined item lifecycle contract
(`onCreate`, `onLoad`, `onSave`, `onDelete`, `itemMetadata`, plus item-definition parts). The
Developer Hub currently implements enough of the contract to render inside Fabric but does
not actually persist item state on the backend. Need to close that gap.

### ⚠️ Planning-First Rule (mandatory before any code changes)
When this workstream is picked up, the first deliverable is a **written plan update in this
file** — not code. Specifically:

1. **Research pass** — search Microsoft samples and docs for how a workload persists items:
   - `https://github.com/microsoft/Microsoft-Fabric-workload-development-sample` (primary reference)
   - Fabric docs: "Workload item lifecycle", "Item CRUD contract", "Item definition parts"
   - Look at the sample's `ItemController` / `ItemCrudController` + the frontend
     `itemApi.create / save / load` wiring
   - Use `mcp_microsoft-lea_microsoft_docs_search` + `mcp_github_search_code` on the
     `microsoft/Microsoft-Fabric-workload-development-sample` repo
2. **Gap analysis** — compare the sample to our current `Backend/agenthub/controllers/`
   and `Frontend/src/controller/` implementation. Identify exactly which endpoints and
   frontend hooks are missing (e.g. `/workload/item/create`, `/workload/item/load`,
   `/workload/item/save`, `/workload/item/delete`, `onCreate` handler in frontend).
3. **Design doc** — append a `### WL-1 Design` subsection to this file covering:
   - Data model: what fields belong in the item definition (workspace picker state?
     fixer presets? remembered BPA rules? chat history reference?)
   - Storage: Fabric-managed item definition (JSON parts) vs. OneLake-backed blob vs.
     external DB. Pick Fabric-managed as default.
   - Permissions model (inherits workspace ACL — confirm)
   - Migration: how existing transient sessions become persisted items on first save
   - Backwards compatibility: item schema versioning
4. **Acceptance criteria** — write concrete checkboxes *before* coding.
5. Only after the design subsection is reviewed by Alexander does the code change start.

### Owns (tentative — finalize in design doc)
- `Backend/agenthub/controllers/item_controller.py` — extend with full CRUD
- `Backend/agenthub/models/item_definition.py` — **NEW** pydantic schema for item parts
- `Frontend/src/controller/ItemApi.ts` — **NEW** wrapper over workload-client item lifecycle
- `Frontend/src/App.tsx` — wire `onCreate` / `onLoad` / `onSave` hooks from workload client
- `Frontend/src/components/ItemEditor.tsx` — restore state from loaded item definition
- Manifest updates (`tools/ManifestGeneratorContainer/` output) to register the item type
  with create/edit/delete intents
- `docker-compose.yaml` / `.env.example` — any new config

### Acceptance (placeholder — refine in design doc)
- [ ] Design subsection approved before any code lands
- [ ] Creating a Developer Hub item from the Fabric "+ New" workspace menu produces a
      real, listable, renamable item
- [ ] Closing the tab and reopening the item from the workspace list restores full state
      (selected workspace, active Fixer page, any in-flight proposals)
- [ ] Deleting the item from the workspace removes all backend-stored state
- [ ] Renaming the item updates both workspace list display name and in-app title
- [ ] Permissions respected: users without workspace access can't load the item
- [ ] No regressions to the transient in-iframe flow (dev-gateway path still works)
- [ ] Works in both AgentHub route (`/agent-hub`) and item-editor route
      (`/agenthub-item-editor/:itemObjectId`)

### Dependencies
None from PBI Fixer workstreams. Can run fully in parallel with any WS-A…WS-F chat.

### Reference materials to pull during research pass
- Microsoft-Fabric-workload-development-sample: item controller + item editor
- `@ms-fabric/workload-client` type defs for `ItemLifecycleContract`
- Existing `ItemEditorRoute` in `Frontend/src/App.tsx` (already wired for URL param, just
  not persisting)
- Our own `.env` — `WORKLOAD_NAME=Org.AgentHub` and the item type declared in the manifest

### Out of scope for WL-1
- Multi-item support (one DeveloperHub-typed item per workspace is enough initially)
- Item sharing across workspaces
- Item-level RBAC beyond what the workspace already provides
- Fixer-specific state (fixer results, BPA findings) — those stay session-local for now;
  revisit once WL-1 lands if user wants them persisted

---

## WL-1 — Research findings + gap analysis (April 24, 2026)

> Per the **Planning-First Rule** above, this subsection captures the research
> pass before any code lands. No code edits in this PR.

### TL;DR
**WL-1 is ~70% already built.** The original problem statement ("today the
item is transient / session-scoped") is **outdated** — the workload's
backend item lifecycle is fully implemented, the manifest declares
`AgentHubItem` as a creatable item type, and the frontend has working
`createItem / loadItem / saveSettings` hooks. What's still missing is
(a) editor-route consistency, (b) durable storage, (c) the option for the
item to also persist **PBI Fixer state**, and (d) validated end-to-end in
the real Fabric portal (not just dev-gateway).

### What ALREADY ships (verified by code read, April 24, 2026)

#### Backend — fully implemented item CRUD
- [`Backend/src/fabric_api/apis/item_lifecycle_api.py`](Backend/src/fabric_api/apis/item_lifecycle_api.py) — auto-generated FastAPI router exposes the four Fabric workload contract endpoints:
  - `POST /workspaces/{wsId}/items/{itemType}/{itemId}` — create
  - `PATCH /workspaces/{wsId}/items/{itemType}/{itemId}` — update (declared elsewhere in the same file)
  - `DELETE /workspaces/{wsId}/items/{itemType}/{itemId}` — delete
  - `GET /workspaces/{wsId}/items/{itemType}/{itemId}/payload` — load payload
- [`Backend/src/fabric_api/impl/item_lifecycle_controller.py`](Backend/src/fabric_api/impl/item_lifecycle_controller.py) — concrete handlers; `SubjectAndAppToken` dual-token auth, tenant-isolation cross-check, `require_subject_token=False` on delete (per Fabric contract).
- [`Backend/src/services/fabric/item_factory.py`](Backend/src/services/fabric/item_factory.py) — registers `AgentHubItem` for `Org.AgentHub.AgentHubItem`.
- [`Backend/src/domain/items/agenthub_item.py`](Backend/src/domain/items/agenthub_item.py) — concrete `ItemBase` with `set_definition / update_definition / get_item_payload`; serializes via `AgentHubMetadata` (pydantic).
- [`Backend/src/services/fabric/item_metadata_store.py`](Backend/src/services/fabric/item_metadata_store.py) — filesystem-backed JSON persistence under `Backend/.data/Org.AgentHub/{tenantId}/{itemId}/{common.json + type-specific.json + jobs/*.json}`. Methods: `upsert / load / exists / delete + upsert_job / load_job / exists_job / delete_job`.

#### Frontend — wired and in use
- [`Frontend/src/controller/AgentHubController.ts`](Frontend/src/controller/AgentHubController.ts) — wraps `workloadClient.controller.callItemCreate / callItemGet / callItemUpdate / callItemDelete`.
- [`Frontend/src/components/AgentHub/ItemContext.tsx`](Frontend/src/components/AgentHub/ItemContext.tsx) — React context that:
  - Auto-loads on mount when `itemObjectId` is present
  - `createItem(name, description)` posts a default `agenthub-metadata` payload
  - `saveSettings()` round-trips updates to Fabric
  - Mirrors session-storage fallback under `agenthub_item_id`
- [`Frontend/src/components/AgentHub/AgentHubLayout.tsx`](Frontend/src/components/AgentHub/AgentHubLayout.tsx) — wraps the AgentHub UI in `<ItemProvider>`.
- [`Frontend/src/App.tsx`](Frontend/src/App.tsx) — declares `/agenthub-item-editor/:itemObjectId` route that mounts `AgentHubLayout` with the URL `itemObjectId`.

#### Manifest — item type registered
- [`Backend/manifest/AgentHubItem.xml`](Backend/manifest/AgentHubItem.xml) — `Item TypeName="${WORKLOAD_NAME}.AgentHubItem"`, `Category="Data"`, `CreateOneLakeFoldersOnArtifactCreation="true"` (auto-creates the OneLake folder), JobScheduler with `ScheduledJob` + `InstantJob`.
- [`Frontend/Package/AgentHubItem.json`](Frontend/Package/AgentHubItem.json) — `editor.path = "/agent-hub/orchestrator"`, `editorTab.maxInstanceCount = 10`, `createItemDialogConfig` wired with `onCreationSuccess / onCreationFailure`, `supportedInDatahubL1 = true`, `supportedInMonitoringHub = true`, `oneLakeCatalogCategory = ["Process"]`. Localized display names in `assets/locales/{en,de,es,fr,...}/translations.json`.

### Sample-vs-current parity (Microsoft-Fabric-workload-development-sample)
The Microsoft sample (.NET / TypeScript) implements the **same four handlers**: `OnCreateItem / OnGetItemPayload / OnUpdateItem / OnDeleteItem`, behind a `SubjectAndAppToken1.0` auth gate. Our Python implementation is a 1:1 port. The sample stores item definitions in an Azure-Storage / SQL backend; we store them on the container filesystem. Functional parity = ✅. Production-grade durability = ❌ (see gap G2 below).

### Real remaining gaps

| ID | Gap | Severity | Owner file | Effort |
|---|---|---|---|---|
| **G1** | **Editor-route mismatch** — manifest declares `editor.path = "/agent-hub/orchestrator"` but `App.tsx` only routes `/agent-hub/*` (catch-all to AgentHubLayout) and `/agenthub-item-editor/:itemObjectId`. When Fabric opens an existing item it currently lands on `/agent-hub/orchestrator?itemObjectId=…` (query-param style, NOT path-param). `AgentHubLayout` accepts `itemObjectId` as a **prop** only — it does not parse it from the query string. → on item-open, the iframe loads but doesn't bind to the saved item. | High | `AgentHubLayout.tsx`, `App.tsx`, `AgentHubItem.json` | S |
| **G2** | **Durability** — `Backend/.data/` lives inside the `agenthub-backend` container. Any `docker compose down -v` or rebuild without a named volume wipes all items. `docker-compose.yaml` does NOT bind-mount or named-volume-mount this path. | High (production) / Low (dev) | `docker-compose.yaml`, optionally migrate to OneLake or `azurite` for dev | S→M |
| **G3** | **PBI Fixer state isn't persisted** — `AgentHubMetadata` only stores `defaultModel / maxRounds / verboseDefault / configuredAgents`. The PBI Fixer's connection bar (workspaceId / datasetId / reportId), active nav key, perspectives drafts, BPA suppressions, and diagram layout all live in `sessionStorage`. Per the WL-1 plan's "Out of scope" line this is acceptable for v1, but worth noting once basics work. | Medium | `agenthub_metadata.py`, `ItemContext.tsx`, PBI Fixer pages | M |
| **G4** | **Item creation dialog UX** — `AgentHubController.ts` comment references `handleCreateAgentHubItem in AgentHubItemCreateDialog`, but **no such dialog file exists** in the frontend. Today users either land on `/agent-hub` (transient mode, no item) or get redirected from Fabric "+ New". The "+ New" path probably works (Fabric uses `createItemDialogConfig`'s default flow), but there's no in-workload dialog if a user wants to spin up a fresh item from inside the editor. Low priority — Fabric's built-in dialog is fine. | Low | new `AgentHubItemCreateDialog.tsx` (only if needed) | S |
| **G5** | **End-to-end test in real Fabric portal** — everything to date has been verified in dev-gateway iframe. Need to actually create a Developer Hub item from `app.powerbi.com` workspace "+ New" menu and confirm: item appears, opens, persists settings change, closes, reopens with same settings, deletes cleanly. | Required | manual + Playwright | M |
| **G6** | **Rename handler** — Fabric calls back into `/items/.../payload` and the `update` endpoint, but the workload doesn't currently observe display-name changes (the name is stored in Fabric metadata, not in our `AgentHubMetadata`). On rename the new name shows up in the workspace list (Fabric-side) but the in-app title may be stale. Verify in G5; fix only if observed. | Low | `ItemContext.tsx` re-fetch on focus | S |
| **G7** | **Permissions check** — `authenticate_control_plane_call` validates the dual token and tenant claim, but we never call `WorkloadAuthorizationService.resolve_permissions(...)` for read/write. Today any authenticated user with a valid Fabric token can call our backend; Fabric-side ACL enforcement protects the item but our DAX/REST proxies don't double-check. | Medium (depth-defense) | `authorization.py` already exists at `Backend/src/services/auth/authorization.py` line 129 — wire it into `agenthub_controller.py` actions | M |

### Updated data model
Already implemented — `AgentHubMetadata` (pydantic) wrapped in payload key `agenthub-metadata`. Future fields (G3) would extend the same model — no schema-version field today, so any extension must be backward-compatible (defaults on optional fields). Recommend **adding `schema_version: int = 1`** field as part of the first WL-1 follow-up edit so future migrations are explicit.

### Storage decision (revised)
- **v1 (recommended now)**: keep filesystem (`Backend/.data/`) + add a **named Docker volume** in `docker-compose.yaml` so it survives container rebuilds. 1-line dev-experience win, no architectural change.
- **v2 (production)**: migrate `ItemMetadataStore` writes to OneLake (the workload already has the `CreateOneLakeFoldersOnArtifactCreation="true"` manifest flag → folder exists per item under `workspaces/{wsId}/{WORKLOAD_NAME}.AgentHubItem/{itemId}/`). Use the existing `onelake_client_service.py` to write `metadata.json` into that folder. Backwards-compatible: `ItemMetadataStore.load` can fall back to filesystem if OneLake fails.

### Permissions model (confirmed)
Inherits workspace ACL via Fabric. Our backend cross-checks tenant claims but does not enforce per-item read/write today. G7 above; accept for v1.

### Migration / backwards compatibility
- Existing transient sessions (`sessionStorage` only) keep working — `ItemContext` falls through to session-only mode when there's no `itemObjectId`.
- Schema versioning: add `schema_version: int = 1` to `AgentHubMetadata` on the first follow-up edit so any future shape changes are explicit.

### Revised acceptance criteria (replaces the placeholder list above)

- [x] **C0** — Research subsection committed before any code change *(done by this edit)*
- [x] **C1** — Fix G1 (editor-route consistency): manifest now points at `/agenthub-item-editor`; `App.tsx` accepts itemObjectId from path or query param.
- [x] **C2** — Fix G2 (durability): `docker-compose.yaml` exports `AGENTHUB_DATA_DIR=/app/data`; `ItemMetadataStore.get_base_directory_path` honours the override so item state lives on the host bind mount and survives `docker compose up -d --force-recreate backend`.
- [x] **C3** — Add `schema_version: int = 1` to `AgentHubMetadata` (forward-compatibility groundwork)
- [ ] **C4** — Manual G5 walk-through in real Fabric portal: create → open → edit settings → close → reopen → verify settings → rename → reopen → verify name → delete → confirm gone from list
- [ ] **C5** — Playwright regression: programmatic create + load via the dev-gateway flow, asserting the round-trip works
- [ ] ~~**C6**~~ — **Deferred** (Fixer state persistence) — out of scope for v1.
- [ ] ~~**C7**~~ — **Deferred** to WL-1.2 (per-item ACL).
- [x] **C8** — Workload version badge `WORKLOAD_VERSION = "v1.0"` shown in topbar; PBI Fixer bumped to `v0.28`. Both visible side-by-side.

### Recommended scope cut for WL-1 v1 (proposal — needs Alexander's OK)
Ship **C1 + C2 + C3 + C4 + C5 + C8** in one cohesive change. Defer C6 (Fixer state persistence) and C7 (per-item ACL) to follow-up workstreams (`WL-1.1` and `WL-1.2` respectively) so this PR stays small and reviewable.

### Effort estimate
- C1: ~30 LOC change in `AgentHubLayout.tsx` + `App.tsx` + maybe `AgentHubItem.json`
- C2: ~3 lines in `docker-compose.yaml`
- C3: 1 line in `agenthub_metadata.py` + matching test
- C4: manual, ~20 min
- C5: ~80 LOC Playwright spec
- C8: 1-line version bump

### Open questions for Alexander before coding starts
1. **Confirm scope cut**: ship the C1+C2+C3+C4+C5+C8 bundle in one PR? Defer C6/C7?
2. **Editor route fix (G1)**: keep manifest path `/agent-hub/orchestrator` and parse `?itemObjectId=` query param inside `AgentHubLayout`, OR change manifest to `/agenthub-item-editor/:itemObjectId` and let the existing route handle it? *(My recommendation: the latter — already routed, cleaner separation.)*
3. **Storage (C2)**: named volume now, OneLake later — agree?
4. **Frontend version**: WL-1 is workload-wide, not PBI-Fixer-specific. Do we still bump the PBI Fixer `vX` badge, or introduce a separate workload version badge?

### Decisions (April 24, 2026 — Alexander)
1. **Scope confirmed**: ship C1+C2+C3+C4+C5+C8. **C6 (Fixer state persistence) deferred** — don't implement if hard. **C7 (per-item ACL) deferred** to WL-1.2.
2. **Editor route**: use the proposed approach — change manifest to point at `/agenthub-item-editor/:itemObjectId` (already routed, cleaner separation).
3. **Storage**: named Docker volume now, OneLake later. ✅
4. **Frontend version**: introduce a **separate workload version badge** ("AgentHub vX.Y") alongside the PBI Fixer version. Both must be visible. Workload version = `WORKLOAD_VERSION` constant exposed in the topbar.

⏸️ ~~Waiting on Alexander's review of this subsection before any code change.~~ → ✅ **Approved. Implementation underway.**
