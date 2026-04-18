# `src/services/`

Stateful, reusable singletons. Every singleton is owned by
[`ServiceRegistry`](../app/core/service_registry.py); module-level
`get_xxx()` accessors only retrieve from the registry — they never create
parallel instances.

## Layout

```
services/
├── configuration_service.py   AppSettings + .env loader (cross-cutting)
├── http_client.py             Shared httpx.AsyncClient (cross-cutting)
├── auth/                      Identity & permission resolution
├── fabric/                    Fabric REST + OneLake data-plane clients
├── agenthub/                  Developer Hub orchestration, persistence, agents
└── mcp/                       MCP client manager (tool proxy)
```

## Per-folder

### `auth/`
| File | Role |
|---|---|
| `open_id_connect_configuration.py` | Caches OIDC metadata + JWKS for JWT validation. |
| `authentication.py` | `SubjectAndAppToken` validation, OBO exchange via MSAL. |
| `authorization.py` | Calls Fabric's permission-resolve API for workspace/item checks. |

### `fabric/`
| File | Role |
|---|---|
| `item_factory.py` | Maps item-type → concrete `BaseItem` subclass. |
| `item_metadata_store.py` | File-based item metadata under `~/.config/<workload>/jobs/...`. |
| `lakehouse_client_service.py` | Fabric REST + OneLake DFS for tables/files. |
| `onelake_client_service.py` | OneLake DFS file operations. |

### `agenthub/`
| File | Role |
|---|---|
| `job_store.py` | SQLite (WAL) persistence. DB path configurable via `AGENTHUB_DB_PATH`; default `~/.config/<workload>/agenthub.db`. |
| `agent_registry.py` | Built-in agent templates (system prompts, tool whitelists). |
| `orchestrator_engine.py` | `OrchestratorEngine` class — plans jobs, runs multi-agent loop, streams events. Retrieved via `get_orchestrator_engine()`. |

### `mcp/`
| File | Role |
|---|---|
| `mcp_client_manager.py` | Spawns MCP subprocesses (configured in `../mcp_servers.json`), discovers tools, routes tool calls. |

### Cross-cutting (root of `services/`)
| File | Role |
|---|---|
| `configuration_service.py` | Loads `appsettings*.json` + env overrides, exposes typed accessors. |
| `http_client.py` | Pooled `httpx.AsyncClient` with timeouts. |

## Rules

- **One DI mechanism**: register in `ServiceRegistry` at startup (`app/core/service_initializer.py`); inject via FastAPI `Depends` or retrieve with the `get_xxx()` accessor.
- **No module-level globals** except the `get_xxx()` accessors and constants (`COPILOT_API_BASE`, etc.).
- **Domain layer isolation**: services may depend on `domain/` but not the other way around.

## Known technical debt

- `orchestrator_engine.py` `configure(...)` mutates the singleton after startup — acceptable for now but should be replaced by constructor injection once MCP manager is itself registered in `ServiceRegistry`.
- Lazy `from services.X import Y` inside methods of `authorization.py`, `lakehouse_client_service.py`, `onelake_client_service.py` hides a circular-import risk — restructure when touching those files.
- `job_store.py` is procedural; wrap in an `AgentHubJobStore` class when next touched.
