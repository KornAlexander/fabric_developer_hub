# Developer Hub — Workload-Level Plan

> Forward-looking, workload-wide roadmap items that sit **outside** the PBI Fixer scope.
> For shipped workload-level history see [CHANGELOG.md](CHANGELOG.md).
> For PBI-Fixer-specific workstreams see [`Frontend/src/components/PbiFixer/PLAN.md`](Frontend/src/components/PbiFixer/PLAN.md).

---

## WL-1 — Persistent Workload Item in Fabric Workspace (final validation)

**Status (May 5 2026):** C1 / C2 / C3 / C8 shipped (see [CHANGELOG.md](CHANGELOG.md)). Two acceptance gates remain before WL-1 v1 closes.

### Open acceptance criteria
- [ ] **C4** — Manual end-to-end walk-through in the real Fabric portal (`app.powerbi.com`): create from workspace "+ New" → open → edit settings → close → reopen → verify settings → rename → reopen → verify name → delete → confirm gone from list. Everything to date verified in dev-gateway iframe only; this is the final production-portal sign-off.
- [ ] **C5** — Playwright regression: programmatic create + load via the dev-gateway flow, asserting the round-trip works. ~80 LOC spec.

### Out of scope for WL-1 v1
- Multi-item support (one DeveloperHub-typed item per workspace is enough initially)
- Item sharing across workspaces
- Item-level RBAC beyond what the workspace already provides

---

## WL-1.1 — Fixer state persistence (deferred from WL-1)

**Goal:** persist PBI Fixer state alongside the AgentHub item so it survives reload / re-login.

`AgentHubMetadata` currently stores `defaultModel / maxRounds / verboseDefault / configuredAgents` only. The PBI Fixer's connection bar (workspaceId / datasetId / reportId), active nav key, perspectives drafts, BPA suppressions, and diagram layout all live in `sessionStorage`. Schema is already versioned (`schema_version: int = 1` added in WL-1 C3) so extension is forward-compatible.

### Tentative owns
- `Backend/.../agenthub_metadata.py` — extend pydantic schema
- `Frontend/src/components/AgentHub/ItemContext.tsx` — wire saved fields back into PBI Fixer pages
- PBI Fixer page components — read initial state from `ItemContext` instead of `sessionStorage`

### Acceptance
- [ ] Connection bar selections persist across reload
- [ ] Active Fixer nav key restored on item reopen
- [ ] Perspectives drafts + BPA suppressions survive
- [ ] No regression to transient-mode (`/agent-hub` without `itemObjectId`)

---

## WL-1.2 — Per-item ACL (deferred from WL-1)

**Goal:** depth-defense by enforcing per-item read/write at the workload backend, not just at Fabric's workspace ACL boundary.

Today `authenticate_control_plane_call` validates the dual token + tenant claim, but our DAX/REST proxies don't double-check workspace permissions. Fabric-side ACL protects the item itself; this workstream extends the same protection to all workload-controller actions.

### Tentative owns
- `Backend/src/services/auth/authorization.py` — `WorkloadAuthorizationService.resolve_permissions(...)` already exists at line 129; wire into controller actions
- `Backend/.../agenthub_controller.py` — call `resolve_permissions` on each mutating action (and on payload reads if hardening reads too)

### Acceptance
- [ ] Authenticated user without workspace read access cannot load an item payload
- [ ] Authenticated user with read-only access cannot trigger mutating endpoints
- [ ] Tenant cross-check still enforced (no regression)
- [ ] Permission failures return Fabric-standard 403 with auditable log line
