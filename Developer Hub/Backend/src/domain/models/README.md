# `src/models/`

**Purpose.** Hand-written Pydantic models for **internal** Developer Hub domain
objects — distinct from [`fabric_api/models/`](../fabric_api/models/) which
contains the (generated) Fabric contract models.

## Files

| File | Contents |
|---|---|
| `authentication_models.py` | `AuthorizationContext`, `Claim`, `TokenVersion`, `SubjectAndAppToken` — populated by `AuthenticationService` after JWT validation. |
| `common_item_metadata.py`  | `CommonItemMetadata` — fields shared by every Fabric item (tenant/workspace/item IDs, display name, last-updated timestamp). |
| `item_metadata.py`         | Wrapper that pairs `CommonItemMetadata` with type-specific metadata. Used by `ItemMetadataStore`. |
| `item_reference.py`        | Minimal `{id, workspaceId}` reference. |
| `fabric_item.py`           | `FabricItem(ItemReference)` — adds type, display name, description, workspace name. Used by lakehouse/Fabric REST responses. |
| `agenthub_metadata.py`     | `AgentHubMetadata` (default model, max rounds, verbose flag) and `ConfiguredAgent`. Stored in the Developer Hub item payload. |
| `agent_models.py`          | Larger Developer Hub orchestration domain: `Job`, `JobStatus`, `AgentTemplate`, `AgentAssignment`, `AgentAction`, `AgentDecision`, `ReasoningPhase`, `UserAgentConfig`, plus enums (`AgentStatus`, `PhaseStatus`, `AgentCategory`). |
| `job_metadata.py`          | `JobMetadata` for Fabric job-instance persistence (job type, instance ID, error details, cancel time). |
| `lakehouse_file.py` / `lakehouse_table.py` / `onelake_folder.py` | Pydantic shapes for Fabric REST payloads consumed by `LakehouseClientService` / `OneLakeClientService`. |
| `write_to_lakehouse_file_request.py` | Request body for the `lakehouse_controller` write endpoint. |

## Feedback

- ⚠️ Two domains coexist: **(a)** Fabric item plumbing (`common_item_metadata`, `item_metadata`, `item_reference`, `fabric_item`, `job_metadata`, `lakehouse_*`) and **(b)** Developer Hub orchestration (`agent_models`, `agenthub_metadata`). Consider splitting into `models/fabric/` and `models/agenthub/`.
- ⚠️ `agent_models.py` is large. Worth splitting per concept (jobs, plans, agents, actions).
- ⚠️ Mix of `model_config = {...}` dict and `model_config = ConfigDict(...)` styles — pick one.
- ⚠️ `JobMetadata.model_dump_json()` returns a `dict`, not a JSON string — the name is misleading. Use Pydantic's built-in `model_dump()`/`model_dump_json()` and remove the alias.
- ⚠️ Several models duplicate fields already present on the generated Fabric models (e.g. `ItemReference` vs. various `*_request.id` fields). Worth auditing for dedup.
