# `src/app/core/`

Application bootstrap plumbing: a tiny DI container and a startup
sequencer. No business logic.

## Files

| File | Contents |
|---|---|
| `service_registry.py` | Thread-safe singleton **DI container** (`ServiceRegistry`). Stores instances and lazy factories, auto-registers `dispose_async`/`close` cleanup hooks, owns service lifecycle. |
| `service_initializer.py` | **Startup orchestrator** invoked from `main.py`'s lifespan. Initialises services in dependency order, parallelising independent ones via `asyncio.gather`. |
| `dependencies.py` | Re-exports services as FastAPI `Annotated[..., Depends(...)]` aliases (`AuthServiceDep`, `ItemFactoryDep`, …) so controllers stay terse. |

## Rules

- `ServiceRegistry` is the **single** DI container. Module-level
  `get_xxx()` accessors in `services/*` only retrieve from it — they do not
  create parallel instances.
- `service_initializer.py` hardcodes the dependency graph. If it grows,
  consider declarative dependencies (each service declares prerequisites).

## Known limitations

- The registry is a process-wide singleton (`_instance` + `Lock`). This
  makes parallel test isolation harder; tests rely on `conftest.py` patching
  to swap services in/out.
