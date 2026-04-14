"""Constants for expected HTTP responses in tests."""

from fastapi import status
from constants.error_codes import ErrorCodes
from fabric_api.models.error_source import ErrorSource


class ExpectedResponses:
    """Expected response codes and error codes for different scenarios."""
    
    # Authentication errors
    MISSING_AUTH_HEADER = {
        "status_code": status.HTTP_401_UNAUTHORIZED,
        "error_code": ErrorCodes.Authentication.AUTH_ERROR,
        "source": ErrorSource.EXTERNAL
    }

    INVALID_AUTH_TOKEN = {
        "status_code": status.HTTP_401_UNAUTHORIZED,
        "error_code": ErrorCodes.Authentication.AUTH_ERROR,
        "source": ErrorSource.EXTERNAL
    }
    
    MISSING_TENANT_ID = {
        "status_code": status.HTTP_401_UNAUTHORIZED,
        "error_code": ErrorCodes.Authentication.AUTH_ERROR,
        "source": ErrorSource.EXTERNAL
    }
    
    AUTH_UI_REQUIRED = {
        "status_code": status.HTTP_401_UNAUTHORIZED,
        "error_code": ErrorCodes.Authentication.AUTH_UI_REQUIRED,
        "source": ErrorSource.EXTERNAL
    }
    
    # Security errors
    ACCESS_DENIED = {
        "status_code": status.HTTP_403_FORBIDDEN,
        "error_code": ErrorCodes.Security.ACCESS_DENIED,
        "source": ErrorSource.USER
    }
    
    # Item errors
    ITEM_NOT_FOUND = {
        "status_code": status.HTTP_404_NOT_FOUND,
        "error_code": ErrorCodes.Item.ITEM_METADATA_NOT_FOUND,
        "source": ErrorSource.SYSTEM
    }
    
    DOUBLED_OPERANDS_OVERFLOW = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": ErrorCodes.Item.DOUBLED_OPERANDS_OVERFLOW,
        "source": ErrorSource.USER
    }
    
    # Item payload errors
    INVALID_ITEM_PAYLOAD = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": ErrorCodes.ItemPayload.INVALID_ITEM_PAYLOAD,
        "source": ErrorSource.USER
    }
    
    MISSING_LAKEHOUSE_REFERENCE = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": ErrorCodes.ItemPayload.MISSING_LAKEHOUSE_REFERENCE,
        "source": ErrorSource.USER
    }
    
    # Internal errors
    INTERNAL_ERROR = {
        "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "error_code": ErrorCodes.INTERNAL_ERROR,
        "source": ErrorSource.SYSTEM
    }
    
    UNEXPECTED_ITEM_TYPE = {
        "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "error_code": ErrorCodes.INTERNAL_ERROR,
        "source": ErrorSource.SYSTEM
    }
    
    # Rate limiting
    TOO_MANY_REQUESTS = {
        "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
        "error_code": ErrorCodes.RateLimiting.TOO_MANY_REQUESTS,
        "source": ErrorSource.SYSTEM
    }
    
    # Kusto errors
    KUSTO_DATA_ERROR = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": ErrorCodes.Kusto.KUSTO_DATA_EXCEPTION,
        "source": ErrorSource.SYSTEM
    }
    
    # Validation errors
    INVALID_PARAMETER = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": "InvalidParameter",
        "source": ErrorSource.USER
    }
    
    INVALID_UUID = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": "InvalidParameter",
        "source": ErrorSource.USER
    }
    
    VALIDATION_ERROR = {
        "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "error_code": "ValidationError",
        "source": ErrorSource.USER
    }
    
    INVALID_REQUEST = {
        "status_code": status.HTTP_400_BAD_REQUEST,
        "error_code": ErrorCodes.INVALID_REQUEST,
        "source": ErrorSource.USER
    }