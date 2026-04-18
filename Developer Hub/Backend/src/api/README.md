# `src/api/`

Hand-written **Developer Hub-specific** REST controllers — the custom APIs the
Developer Hub frontend calls. These are **not** part of the Fabric → Workload
contract (those live in [`../fabric_api/impl/`](../fabric_api/impl/)).

## Files

| File | Router prefix | Responsibility |
|---|---|---|
| `agenthub_controller.py`    | `/api`        | Developer Hub jobs (create/list/cancel), agent templates & user configs, plan generation/approval, SSE event stream. |
| `github_chat_controller.py` | `/api/github` | GitHub OAuth device flow, GitHub→Copilot token exchange, model list, chat-completions proxy, agentic tool-call loop. |
| `lakehouse_controller.py`   | (none)        | Lakehouse helpers: list tables/files, read/write file. |
| `onelake_controller.py`     | (none)        | OneLake helpers (e.g. `isOneLakeSupported`). |

## Conventions

- **Path-param types are `UUID`** wherever the value is a GUID (job_id,
  config_id, workspace_id, lakehouse_id). FastAPI returns 422 for invalid
  values without any custom handling.
- **Injected dependencies** (`AuthenticationService`, `LakehouseClientService`,
  …) come from `app/core/dependencies.py` via `Depends`.
- **No business logic**: controllers translate HTTP ↔ service calls and
  nothing else.

## Known debt

- `_user_id_from_request` in `agenthub_controller.py` hashes the
  Authorization header as a placeholder user ID. Replace with JWT-claim
  extraction once the Fabric token validation path is stable.
- `agenthub_controller.py` imports private helpers (`_get_copilot_token`,
  `_acquire_mcp_tokens`) from `github_chat_controller.py` and also wires them
  into `OrchestratorEngine.configure(...)` in `main.py`. Pick one path.
- `github_chat_controller.py` is large — consider extracting the Copilot
  client into `services/github/copilot_client.py` when next touched.
