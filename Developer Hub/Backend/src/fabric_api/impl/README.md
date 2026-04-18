# `src/fabric_api/impl/`

**Purpose.** Hand-written concrete implementations of the abstract Fabric API
base classes from [`../apis/`](../apis/). This is where the actual handlers
live for the Fabric → Workload contract.

## Files

| File | Implements | Responsibility |
|---|---|---|
| `item_lifecycle_controller.py` | `BaseItemLifecycleApi` | Create/update/delete/get-payload for Developer Hub items. Authenticates, calls `ItemFactory`, delegates to the item class. |
| `jobs_controller.py`           | `BaseJobsApi`          | Start/cancel/poll job instances. Spawns `asyncio.Task` per job, tracks them in `_background_tasks` for graceful shutdown. |
| `endpoint_resolution_controller.py` | `BaseEndpointResolutionApi` | Returns the workload's service endpoint URL based on tenant/workspace region. |

These were originally generator stubs; everything inside the methods is
hand-written and **must not** be regenerated.

## Feedback

- ✅ Thin controllers — they delegate to services and item classes, no business logic inline.
- ⚠️ `_background_tasks` in `jobs_controller.py` is a module-level set. That's fine but couples background-task tracking to this file; a small `BackgroundTaskRegistry` service would be testable and reusable.
- ⚠️ All three controllers re-import services via `get_authentication_service()` etc. instead of using the FastAPI DI shortcuts in [`core/dependencies.py`](../../core/dependencies.py). Inconsistent with [`src/impl/`](../../impl/README.md) which uses `Depends`.
