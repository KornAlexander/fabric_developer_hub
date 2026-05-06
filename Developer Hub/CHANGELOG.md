# Developer Hub — Workload-Level Changelog

> Implementation history for shipped workload-wide workstreams. Forward-looking work lives in [PLAN.md](PLAN.md).
> For PBI-Fixer-specific history see [`Frontend/src/components/PbiFixer/CHANGELOG.md`](Frontend/src/components/PbiFixer/CHANGELOG.md).

---

## WL-1 — Persistent Workload Item in Fabric Workspace (April–May 2026)

**Goal:** make the Developer Hub item persistently created in the Fabric workspace where the user launches it. Survives browser reloads, tenant re-logins, and shows up in the workspace item list with its own icon + rename + delete + permissions semantics, like a Notebook or Lakehouse.

### Research pass (April 24, 2026)
Discovered that **WL-1 was already ~70% built** when the workstream was opened — backend item lifecycle, manifest item-type registration, and frontend `createItem / loadItem / saveSettings` hooks were all in place. The original "today the item is transient / session-scoped" framing was outdated. Research subsection captured a real gap analysis (G1–G7) instead of greenfield design.

**Already-shipped surfaces verified by code read:**
- Backend item CRUD — `Backend/src/fabric_api/apis/item_lifecycle_api.py`, `Backend/src/fabric_api/impl/item_lifecycle_controller.py`, `Backend/src/services/fabric/item_factory.py` (registers `AgentHubItem` for `Org.AgentHub.AgentHubItem`), `Backend/src/domain/items/agenthub_item.py` (`set_definition / update_definition / get_item_payload`), `Backend/src/services/fabric/item_metadata_store.py` (filesystem-backed JSON under `Backend/.data/Org.AgentHub/{tenantId}/{itemId}/`).
- Frontend wired and in use — `Frontend/src/controller/AgentHubController.ts` wraps `workloadClient.controller.callItemCreate / callItemGet / callItemUpdate / callItemDelete`; `Frontend/src/components/AgentHub/ItemContext.tsx` auto-loads on mount, `createItem(name, description)` posts default `agenthub-metadata` payload, `saveSettings()` round-trips updates; `AgentHubLayout.tsx` wraps in `<ItemProvider>`; `App.tsx` declares `/agenthub-item-editor/:itemObjectId`.
- Manifest — `Backend/manifest/AgentHubItem.xml` (`Item TypeName="${WORKLOAD_NAME}.AgentHubItem"`, `Category="Data"`, `CreateOneLakeFoldersOnArtifactCreation="true"`, JobScheduler with `ScheduledJob` + `InstantJob`); `Frontend/Package/AgentHubItem.json` (`editor.path = "/agent-hub/orchestrator"`, `editorTab.maxInstanceCount = 10`, `createItemDialogConfig`, `supportedInDatahubL1`, `supportedInMonitoringHub`, `oneLakeCatalogCategory = ["Process"]`, localized display names).

**Sample-vs-current parity (Microsoft-Fabric-workload-development-sample):** 1:1 port of the four-handler contract (`OnCreateItem / OnGetItemPayload / OnUpdateItem / OnDeleteItem`) behind `SubjectAndAppToken1.0` auth gate. Functional parity ✅. Sample uses Azure Storage / SQL; we use container filesystem (production durability addressed in C2).

### Decisions (April 24, 2026 — Alexander)
1. **Scope**: ship C1 + C2 + C3 + C4 + C5 + C8. C6 (Fixer state persistence) deferred — don't implement if hard. C7 (per-item ACL) deferred to WL-1.2.
2. **Editor route**: change manifest to point at `/agenthub-item-editor/:itemObjectId` (already routed, cleaner separation) — chosen over keeping `/agent-hub/orchestrator` and parsing query param.
3. **Storage**: named Docker volume now, OneLake later.
4. **Frontend version**: separate workload version badge ("AgentHub vX.Y") alongside the PBI Fixer version. Both must be visible. Workload version = `WORKLOAD_VERSION` constant exposed in topbar.

### Shipped acceptance criteria

- ✅ **C0** — Research subsection committed before code change.
- ✅ **C1** — Editor-route consistency (G1): manifest now points at `/agenthub-item-editor`; `App.tsx` accepts `itemObjectId` from path or query param.
- ✅ **C2** — Durability (G2): `docker-compose.yaml` exports `AGENTHUB_DATA_DIR=/app/data`; `ItemMetadataStore.get_base_directory_path` honours the override so item state lives on the host bind mount and survives `docker compose up -d --force-recreate backend`.
- ✅ **C3** — `schema_version: int = 1` added to `AgentHubMetadata` (forward-compatibility groundwork for any future shape changes).
- ✅ **C8** — Workload version badge `WORKLOAD_VERSION = "v1.0"` shown in topbar; PBI Fixer bumped to `v0.28`. Both visible side-by-side.

### Deferred — not part of WL-1 v1
- **C6** — Fixer state persistence (workspaceId / datasetId / reportId, active nav key, perspectives drafts, BPA suppressions, diagram layout — currently `sessionStorage`-only). Out of scope for v1; revisit if user wants them persisted.
- **C7** — Per-item ACL (depth-defense — call `WorkloadAuthorizationService.resolve_permissions(...)` from `agenthub_controller.py` actions; `authorization.py` already exists at `Backend/src/services/auth/authorization.py:129`). Tracked as **WL-1.2**.
- **G3** (PBI Fixer state in `AgentHubMetadata`) — same as C6.
- **G4** — In-workload item-creation dialog. Fabric's built-in `createItemDialogConfig` flow is sufficient.
- **G6** — Rename handler observation (Fabric-side rename works; in-app title may be stale until refresh). Verify in C4 and only fix if observed.
