from uuid import UUID
from fastapi import Request, FastAPI
import logging
from exceptions.base_exception import WorkloadExceptionBase
from exceptions.exceptions import (
    UnauthorizedException,
    TooManyRequestsException,
    InternalErrorException,
    InvariantViolationException,
    DoubledOperandsOverflowException,
    ItemMetadataNotFoundException,
    AuthenticationException,
    AuthenticationUIRequiredException,
    InvalidParameterException
)

logger = logging.getLogger(__name__)

async def workload_exception_handler(request: Request, exc: WorkloadExceptionBase):
    """
    Handle all workload-specific exceptions
    """
    logger.error(f"Workload exception: {exc}\r\n{exc.to_telemetry_string()}")
    return exc.to_response()

async def unauthorized_exception_handler(request: Request, exc: UnauthorizedException):
    """Handle unauthorized exceptions."""
    logger.error(f"Unauthorized access: {str(exc)}")
    return exc.to_response()

async def too_many_requests_exception_handler(request: Request, exc: TooManyRequestsException):
    """Handle rate limiting exceptions."""
    logger.warning(f"Rate limiting: {str(exc)}")
    return exc.to_response()

async def internal_error_exception_handler(request: Request, exc: InternalErrorException):
    """Handle internal server errors."""
    logger.error(f"Internal error: {exc.to_telemetry_string()}")
    return exc.to_response()

async def doubled_operands_overflow_exception_handler(request: Request, exc: DoubledOperandsOverflowException):
    """Handle doubled operands overflow errors."""
    logger.warning(f"Doubled operands overflow: {str(exc)}")
    return exc.to_response()

async def item_metadata_not_found_exception_handler(request: Request, exc: ItemMetadataNotFoundException):
    """Handle item metadata not found errors."""
    logger.warning(f"Item metadata not found: {str(exc)}")
    return exc.to_response()

async def authentication_exception_handler(request: Request, exc: AuthenticationException):
    """Handle authentication errors."""
    logger.error(f"Authentication error: {str(exc)}")
    return exc.to_response()

async def authentication_ui_required_exception_handler(request: Request, exc: AuthenticationUIRequiredException):
    logger.error("Failed to acquire a token, user interaction is required, returning '401 Unauthorized' with WWW-Authenticate header")
    response = exc.to_response()
    # Add WWW-Authenticate header
    response.headers["WWW-Authenticate"] = exc.to_www_authenticate_header()
    return response

async def value_error_handler(request: Request, exc: ValueError):
    """Handle ValueError exceptions by converting to InvalidParameterException."""
    logger.error(f"ValueError caught: {str(exc)}")
    
    error_message = str(exc)
    parameter_name = "unknown"
    
    # Try to extract parameter info from the request path
    path_params = request.path_params
    
    # Common ValueError patterns
    if "badly formed hexadecimal UUID string" in error_message:
        # Try to identify which UUID parameter failed
        for param_name, param_value in path_params.items():
            if "id" in param_name.lower():
                try:
                    UUID(str(param_value))
                except ValueError:
                    parameter_name = param_name
                    break
        else:
            parameter_name = "UUID"
    elif "invalid literal for int()" in error_message:
        parameter_name = "integer value"
    elif "could not convert string to float" in error_message:
        parameter_name = "numeric value"
    
    # Create InvalidParameterException
    invalid_param_exc = InvalidParameterException(
        parameter_name=parameter_name,
        message=error_message
    )
    
    # Return the formatted response
    return invalid_param_exc.to_response()

async def invariant_violation_exception_handler(request: Request, exc: InvariantViolationException):
    """Handle invariant violation errors."""
    logger.error(f"Invariant violation: {exc.to_telemetry_string()}")
    return exc.to_response()

async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unknown exception: {exc}")
    # Return InternalErrorException response
    internal_error = InternalErrorException("Unexpected error")
    return internal_error.to_response()

def register_exception_handlers(app: FastAPI):
    """
    Register all exception handlers with the FastAPI app.
    """
    # Register specific handlers for better logging control
    app.add_exception_handler(AuthenticationUIRequiredException, authentication_ui_required_exception_handler)
    app.add_exception_handler(AuthenticationException, authentication_exception_handler)
    app.add_exception_handler(UnauthorizedException, unauthorized_exception_handler)
    app.add_exception_handler(TooManyRequestsException, too_many_requests_exception_handler)
    app.add_exception_handler(InvariantViolationException, invariant_violation_exception_handler)
    app.add_exception_handler(InternalErrorException, internal_error_exception_handler)
    app.add_exception_handler(DoubledOperandsOverflowException, doubled_operands_overflow_exception_handler)
    app.add_exception_handler(ItemMetadataNotFoundException, item_metadata_not_found_exception_handler)
    app.add_exception_handler(ValueError, value_error_handler)

    # Register base handler as fallback for any WorkloadExceptionBase we didn't explicitly handle
    app.add_exception_handler(WorkloadExceptionBase, workload_exception_handler)
    
    # Register global exception handler for all other exceptions
    app.add_exception_handler(Exception, global_exception_handler)
