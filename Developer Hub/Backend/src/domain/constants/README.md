# `src/constants/`

**Purpose.** Centralised, hand-maintained constants — environment URLs, OAuth scopes,
HTTP header names, error codes, and workload identifiers. Imported everywhere
instead of re-declaring magic strings.

## Files

| File | Contents |
|---|---|
| `environment_constants.py` | Public Azure/Fabric URLs and well-known AAD app IDs (e.g. `FABRIC_API_BASE_URL`, `AAD_INSTANCE_URL`). |
| `api_constants.py` | Composed API URLs derived from `EnvironmentConstants` (e.g. `WORKLOAD_CONTROL_API_BASE_URL`). |
| `http_constants.py` | HTTP header name constants (`Authorization`, `x-ms-client-tenant-id`, …) and `AuthorizationSchemes.BEARER`. |
| `onelake_constants.py` | OneLake OAuth scopes (`https://storage.azure.com/.default`). |
| `workload_constants.py` | `WORKLOAD_NAME` (from env, default `Org.AgentHub`) and the single item type `AGENTHUB_ITEM`. |
| `workload_scopes.py` | OAuth scopes exposed by the workload's Entra app registration — **must** match the app registration strings. |
| `error_codes.py` | Nested string constants for error codes returned to Fabric. |

## Feedback

- ✅ Clean separation; this is the right pattern (Python equivalent of a `Constants.cs` namespace).
- ⚠️ `workload_scopes.py` still uses `AGENTHUB_*` names that map to a literal `"Item1.ReadWrite.All"` string. Either rename the literal in the Entra app registration (cleaner), or rename the Python symbol to `ITEM1_READ_WRITE_ALL` so the contract is obvious.
- ⚠️ `error_codes.py` uses nested classes purely as a namespace. Idiomatic Python would use either nested `Enum`s or flat `ERROR_CODE_*` constants. Keep as-is unless you intend to iterate them.
- 💡 Consider a `__all__` in each module to make wildcard imports explicit (currently none).
