# `src/fabric_api/`

**Purpose.** Implements the **Fabric → Workload contract** (the API Microsoft
Fabric calls into your workload). Most files here are **generated** from
[`openapi.yaml`](../../openapi.yaml) by
[openapi-generator](https://openapi-generator.tech/) using the
`python-fastapi` template. See [`REGENERATE.md`](../../REGENERATE.md).

## Subfolders

| Folder | Status | Contents |
|---|---|---|
| `apis/`   | **Generated** — do not hand-edit | FastAPI routers + abstract base classes for each endpoint group: `item_lifecycle_api.py`, `jobs_api.py`, `endpoint_resolution_api.py`. |
| `models/` | **Generated** — do not hand-edit | Pydantic models for request/response payloads (`CreateItemRequest`, `ItemJobInstanceState`, `ErrorResponse`, …). |
| `impl/`   | Generated stubs, then **hand-edited** | Concrete subclasses of the abstract API base classes. Business logic for the three controllers lives here. |

## Other files

| File | Contents |
|---|---|
| `security_api.py` | Empty stub from openapi-generator (re-exports FastAPI security primitives). Effectively dead code. |

## Endpoints exposed

| Method | Path | Controller |
|---|---|---|
| POST   | `/workspaces/{w}/items/{type}/{id}` | `item_lifecycle_create_item` |
| PATCH  | `/workspaces/{w}/items/{type}/{id}` | `item_lifecycle_update_item` |
| DELETE | `/workspaces/{w}/items/{type}/{id}` | `item_lifecycle_delete_item` |
| GET    | `/workspaces/{w}/items/{type}/{id}/payload` | `item_lifecycle_get_item_payload` |
| POST   | `/workspaces/{w}/items/{type}/{id}/jobTypes/{jt}/instances/{ji}` | `jobs_create_item_job_instance` |
| POST   | `/workspaces/{w}/items/{type}/{id}/jobTypes/{jt}/instances/{ji}/cancel` | `jobs_cancel_item_job_instance` |
| GET    | `/workspaces/{w}/items/{type}/{id}/jobTypes/{jt}/instances/{ji}` | `jobs_get_item_job_instance_state` |
| POST   | `/resolve-api-path-placeholder` | `endpoint_resolution_resolve` |

## Feedback

- ✅ Clear contract boundary — Fabric talks to this dir, Developer Hub features live in [`src/api/`](../api/README.md).
- ⚠️ `apis/` files have noisy openapi-generator boilerplate (unused imports with `# noqa: F401`, duplicate `200:` keys in `responses` dicts). That's a generator bug; harmless but ugly. **Don't** hand-fix — it'll get clobbered on regen.
- 💡 Add `apis/` and `models/` to `pyproject.toml` ruff `extend-exclude` so they don't pollute lint reports.
- ⚠️ `security_api.py` is dead. Safe to delete (or leave; regenerator will recreate it).
