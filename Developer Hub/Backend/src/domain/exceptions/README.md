# `src/exceptions/`

**Purpose.** Workload-specific exception hierarchy that maps cleanly to the
Fabric `ErrorResponse` schema (status code + error code + structured details).

## Files

| File | Contents |
|---|---|
| `base_exception.py` | `WorkloadExceptionBase` — carries `http_status_code`, `error_code`, message template + parameters, `ErrorSource`, `is_permanent`, and structured `details`. Has `to_response()` returning a FastAPI `JSONResponse` shaped like `ErrorResponse`. |
| `exceptions.py` | Concrete subclasses: `InternalErrorException`, `InvariantViolationException`, `UnauthorizedException`, `AuthenticationException`, `AuthenticationUIRequiredException`, `ItemMetadataNotFoundException`, `TooManyRequestsException`, `InvalidParameterException`, `DoubledOperandsOverflowException`, etc. |

Used by [`app/exception_handlers.py`](../../app/exception_handlers.py)
to convert exceptions into Fabric-compliant HTTP responses.

## Feedback

- ✅ Excellent pattern: every domain error has a dedicated class with the right HTTP status preset.
- ⚠️ `DoubledOperandsOverflowException` is a leftover from the calculator-workload sample; Developer Hub doesn't compute anything. Remove it and its handler.
- ⚠️ `to_telemetry_string()` exists on some classes (e.g. `InternalErrorException`) but not on the base — inconsistent. Either lift it to `WorkloadExceptionBase` or make it abstract.
- 💡 Consider one file per exception (or grouping by domain — auth, item, rate-limiting). `exceptions.py` will keep growing.
