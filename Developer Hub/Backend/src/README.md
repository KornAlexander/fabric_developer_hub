# `Developer Hub/Backend/src/`

The Python FastAPI backend for the Fabric Developer Hub workload. This README is
the **map of the source tree**. Every folder has its own README.

## Entrypoints

| File | Role |
|---|---|
| `main.py` | FastAPI app factory, lifespan (startup/shutdown), uvicorn entry. |
| `app/bootstrap.py` | Loads `Developer Hub/.env` once at process start. **Imported first** in `main.py` and `tests/conftest.py`. |
| `appsettings.json` / `appsettings.Development.json` | Non-secret app defaults (server, logging, storage). Identity/secrets come from `.env`. |
| `mcp_servers.json` | Stdio-MCP server registry consumed by `MCPClientManager`. |

## Layout

```
src/
├── main.py                  FastAPI entrypoint
├── app/                     Application framework (DI, bootstrap, handlers)
│   ├── bootstrap.py         single find_dotenv() call
│   ├── core/                ServiceRegistry + ServiceInitializer
│   └── exception_handlers.py
├── api/                     Hand-written HTTP routes (Developer Hub custom)
│   ├── agenthub_controller.py
│   ├── github_chat_controller.py
│   ├── lakehouse_controller.py
│   └── onelake_controller.py
├── fabric_api/              Fabric→Workload contract (openapi-generated)
│   ├── apis/   (gen)        route stubs
│   ├── models/ (gen)        request/response DTOs
│   └── impl/                Fabric lifecycle controllers (hand-written)
├── domain/                  Pure-Python domain layer (no I/O)
│   ├── constants/           URLs, OAuth scopes, header names, error codes
│   ├── exceptions/          WorkloadExceptionBase hierarchy
│   ├── items/               AgentHubItem + BaseItem
│   └── models/              Pydantic domain models (auth, agents, jobs)
├── services/                Stateful singletons (all in ServiceRegistry)
│   ├── auth/                authentication, authorization, OpenID
│   ├── fabric/              item_factory, metadata_store, lakehouse, onelake
│   ├── agenthub/            orchestrator_engine, agent_registry, job_store
│   ├── mcp/                 mcp_client_manager
│   ├── configuration_service.py
│   └── http_client.py
└── mcp_servers/             Stdio MCP subprocesses (fabric, semantic_link)
```

## Design principles

- **Single DI container.** `ServiceRegistry` (via `ServiceInitializer`) owns every singleton. `get_xxx()` accessors only retrieve from the registry — they never create parallel instances.
- **Domain layer has no I/O.** `domain/` depends on nothing inside `services/` or `app/`.
- **Fabric contract is generated.** Never hand-edit `fabric_api/apis/` or `fabric_api/models/`; regenerate from `openapi.yaml` (see `../REGENERATE.md`).
- **Hand-written routes in `api/`.** These are Developer Hub–specific APIs the Fabric contract doesn't cover.
- **Secrets only in `.env`.** `appsettings*.json` must never contain identity/secrets.

## Known gaps

- No tests for `services/agenthub/orchestrator_engine.py`, `services/mcp/mcp_client_manager.py`, or `services/agenthub/job_store.py`.
- `_user_id_from_request` in `api/agenthub_controller.py` hashes the Authorization header — replace with JWT-claim extraction once the Fabric token validation path matures.
- Lazy `from services.X import Y` inside methods in `services/auth/authorization.py`, `services/fabric/lakehouse_client_service.py`, `services/fabric/onelake_client_service.py` hides a circular-import risk; restructure when touching those files.
