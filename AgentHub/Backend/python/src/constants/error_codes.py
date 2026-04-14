class ErrorCodes:
    """Error codes for different types of errors."""
    
    INTERNAL_ERROR = "InternalError"
    INVALID_REQUEST = "InvalidRequest"
    INVALID_PARAMETER = "InvalidParameter"
    
    class Authentication:
        AUTH_UI_REQUIRED = "AuthUIRequired"
        AUTH_ERROR = "AuthError"
    
    class Security:
        ACCESS_DENIED = "AccessDenied"
    
    class ItemPayload:
        INVALID_ITEM_PAYLOAD = "InvalidItemPayload"
        MISSING_LAKEHOUSE_REFERENCE = "MissingLakehouseReference"
    
    class RateLimiting:
        TOO_MANY_REQUESTS = "TooManyRequests"
    
    class Item:
        ITEM_METADATA_NOT_FOUND = "ItemMetadataNotFound"
        DOUBLED_OPERANDS_OVERFLOW = "DoubledOperandsOverflow"
    
    class Kusto:
        KUSTO_DATA_EXCEPTION = "KustoDataException"