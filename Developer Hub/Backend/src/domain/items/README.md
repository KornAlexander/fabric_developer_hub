# `src/items/`

**Purpose.** Domain classes representing **Fabric workspace items**. Each
subclass corresponds to one item type registered in the workload manifest and
implements the lifecycle hooks Fabric calls (create/update/delete/get payload,
plus job execution).

## Files

| File | Contents |
|---|---|
| `base_item.py`     | `ItemBase[TItemMetadata, TItemClientMetadata]` — abstract base. Holds `auth_context`, IDs, display name, and metadata-store/auth/onelake service refs. Defines `load`, `create`, `update`, `delete`, `get_item_payload`, plus abstract `item_type`, `get_metadata_class`, `execute_job`, `get_job_state`. |
| `agenthub_item.py` | `AgentHubItem` — the only registered item type today. Persists `AgentHubMetadata` (default model, max rounds, configured agents) under the `"agenthub-metadata"` key in the item payload. Job execution is a no-op (the `orchestrator_engine` runs jobs separately). |

## Feedback

- ✅ Clean abstract/concrete pair. Adding a new item type is a clear extension point: subclass `ItemBase`, register it in `ItemFactory`, add a manifest entry.
- ⚠️ Only one item type exists; the `[TItemMetadata, TItemClientMetadata]` generics are over-engineered for that. Keep if you plan more types soon, otherwise simplify.
- ⚠️ `base_item.py` does function-local imports of services to avoid circular imports. Symptom of services and items being coupled both ways. Consider moving the `auth_context`-using methods up into a thin orchestrator layer so items become pure domain objects.
- ⚠️ The folder name (`items`) is generic. If the project grows multiple item types, prefer `items/` containing one subfolder per type with its metadata, item class, and tests colocated.
