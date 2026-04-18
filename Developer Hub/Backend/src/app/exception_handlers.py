"""Exception handlers that map workload exceptions to Fabric-shaped JSON responses.

One handler covers every `WorkloadExceptionBase` subclass via `isinstance`
dispatch. A couple of exceptions need extra behaviour (custom log level or
`WWW-Authenticate` header) so they are handled individually.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import FastAPI, Request

from domain.exceptions.base_exception import WorkloadExceptionBase
from domain.exceptions.exceptions import (
    AuthenticationUIRequiredException,
    InternalErrorException,
    InvalidParameterException,
    TooManyRequestsException,
)

logger = logging.getLogger(__name__)


async def workload_exception_handler(request: Request, exc: WorkloadExceptionBase):
    """Default handler for every workload exception.

    Logs at an appropriate level and returns the Fabric-shaped JSON response.
    """
    level = logging.WARNING if exc.http_status_code < 500 else logging.ERROR
    telemetry = exc.to_telemetry_string() if hasattr(exc, "to_telemetry_string") else str(exc)
    logger.log(level, "%s: %s", type(exc).__name__, telemetry)
    return exc.to_response()


async def too_many_requests_exception_handler(request: Request, exc: TooManyRequestsException):
    """Rate-limit logs at WARNING even though status is 4xx (separate metric)."""
    logger.warning("Rate limit hit: %s", exc)
    return exc.to_response()


async def authentication_ui_required_exception_handler(
    request: Request, exc: AuthenticationUIRequiredException
):
    """401 with WWW-Authenticate header so Fabric can trigger interactive auth."""
    logger.error("UI authentication required — returning 401 with WWW-Authenticate")
    response = exc.to_response()
    response.headers["WWW-Authenticate"] = exc.to_www_authenticate_header()
    return response


async def value_error_handler(request: Request, exc: ValueError):
    """Convert stray `ValueError`s (e.g. malformed UUIDs slipping past typing)
    into an `InvalidParameterException`.

    Hand-written routes should declare `UUID` types so FastAPI raises a 422
    with parameter context before we reach here. This is only a safety net
    for generated routes that still do `UUID(str)` inside the handler body;
    in that case we try to infer which path parameter failed.
    """
    message = str(exc)
    param_name = "unknown"

    if "badly formed hexadecimal UUID string" in message:
        for name, value in request.path_params.items():
            try:
                UUID(str(value))
            except ValueError:
                param_name = name
                break
        else:
            param_name = "UUID"

    logger.warning("ValueError at %s (param=%s): %s", request.url.path, param_name, message)
    return InvalidParameterException(parameter_name=param_name, message=message).to_response()


async def global_exception_handler(request: Request, exc: Exception):
    """Last-resort handler — do not leak internal details."""
    logger.exception("Unhandled exception at %s", request.url.path)
    return InternalErrorException("Unexpected error").to_response()


def register_exception_handlers(app: FastAPI) -> None:
    """Wire exception handlers in specificity order (most specific first)."""
    app.add_exception_handler(AuthenticationUIRequiredException, authentication_ui_required_exception_handler)
    app.add_exception_handler(TooManyRequestsException, too_many_requests_exception_handler)
    app.add_exception_handler(WorkloadExceptionBase, workload_exception_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(Exception, global_exception_handler)
