# `src/app/`

Application-framework plumbing. Nothing here is domain logic.

| Item | Purpose |
|---|---|
| `bootstrap.py` | Calls `load_dotenv(find_dotenv(usecwd=True))` exactly once. Must be the **first import** in any entrypoint (`main.py`, `tests/conftest.py`) so `os.environ` is populated before anything else runs. |
| `core/` | `ServiceRegistry` (thread-safe singleton container) and `ServiceInitializer` (ordered startup sequencer). See [`core/README.md`](core/README.md). |
| `exception_handlers.py` | FastAPI exception handlers that map Python exceptions to Fabric-compliant `ErrorResponse` bodies. Registered via `register_exception_handlers(app)`. |

## Design

- Exactly one handler for the whole `WorkloadExceptionBase` hierarchy, plus
  targeted handlers for `RateLimitException`, `AuthenticationUIRequiredException`,
  `ValidationError`, `ValueError`, and the fallback `Exception`.
- `value_error_handler` still does UUID-parameter-name inference because a
  handful of generated routes take `str` and convert in-body; hand-written
  routes in `src/api/` use `UUID` path types so FastAPI returns 422 directly.

## Do not

- Import anything from `services.*` or `api.*` here — this folder must stay
  at the bottom of the import graph.
- Put business logic in `exception_handlers.py`. If a handler needs to know
  something domain-specific, raise a more specific `WorkloadExceptionBase`
  subclass instead.
