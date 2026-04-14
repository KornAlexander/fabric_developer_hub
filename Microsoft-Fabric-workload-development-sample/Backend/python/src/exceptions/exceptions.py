from uuid import UUID
from fastapi import status
from typing import List, Optional

from exceptions.base_exception import WorkloadExceptionBase
from fabric_api.models.error_source import ErrorSource
from constants.error_codes import ErrorCodes

class InternalErrorException(WorkloadExceptionBase):
    """Exception for internal errors."""
    
    def __init__(self, message: str):
        super().__init__(
            http_status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=ErrorCodes.INTERNAL_ERROR,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.SYSTEM,
            is_permanent=False
        )
        self.internal_message = message
        
    def to_telemetry_string(self) -> str:
        return self.internal_message

class InvariantViolationException(InternalErrorException):
    """Exception for invariant violations."""
    
    def __init__(self, message: str):
        super().__init__(message)
        
    def to_telemetry_string(self) -> str:
        return f"INVARIANT VIOLATION: {self.internal_message}"

class InvalidRelativePathException(InternalErrorException):
    """Exception for invalid relative paths."""
    
    def __init__(self, relative_path: str):
        super().__init__(f"The relative path is invalid: {relative_path}")

class UnexpectedItemTypeException(InternalErrorException):
    """Exception for unexpected item types."""
    
    def __init__(self, message: str):
        super().__init__(message)

class UnauthorizedException(WorkloadExceptionBase):
    """Exception for access denied situations."""
    
    def __init__(self, message: str = "Access denied"):
        super().__init__(
            http_status_code=status.HTTP_403_FORBIDDEN,
            error_code=ErrorCodes.Security.ACCESS_DENIED,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.USER,
            is_permanent=True
        )

class AuthenticationException(WorkloadExceptionBase):
    """Exception for authentication errors."""
    
    def __init__(self, message: str):
        super().__init__(
            http_status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCodes.Authentication.AUTH_ERROR,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.EXTERNAL,
            is_permanent=False
        )

class AuthenticationUIRequiredException(WorkloadExceptionBase):
    """Exception raised when UI authentication is required."""
    
    ADDITIONAL_SCOPES_TO_CONSENT_NAME = "additionalScopesToConsent"
    CLAIMS_FOR_CONDITIONAL_ACCESS_POLICY_NAME = "claimsForConditionalAccessPolicy"
    
    def __init__(self, message: str):
        super().__init__(
            http_status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCodes.Authentication.AUTH_UI_REQUIRED,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.SYSTEM,
            is_permanent=False
        )
        # Initialize private attributes to store claims and scopes
        self._claims_for_conditional_access = None
        self._additional_scopes_to_consent = None
        
    @property
    def claims_for_conditional_access_policy(self) -> Optional[str]:
        """Get claims for conditional access policy from Details, matching C# implementation."""
        if self.details and len(self.details) > 0:
            # Look for the claims in the first detail's additional parameters
            for param in self.details[0].additional_parameters or []:
                if param.name == self.CLAIMS_FOR_CONDITIONAL_ACCESS_POLICY_NAME:
                    return param.value
        return self._claims_for_conditional_access
        
    def add_claims_for_conditional_access(self, claims: str) -> 'AuthenticationUIRequiredException':
        """Add claims for conditional access."""
        self._claims_for_conditional_access = claims  # Store the raw claims
        self.with_detail(
            "conditionalAccess", 
            "{0}", 
            (self.CLAIMS_FOR_CONDITIONAL_ACCESS_POLICY_NAME, claims)
        )
        return self
        
    def add_scopes_to_consent(self, scopes: List[str]) -> 'AuthenticationUIRequiredException':
        """Add scopes that need consent."""
        self._additional_scopes_to_consent = scopes  # Store the raw scopes list
        self.with_detail(
            "scopesToConsent", 
            "{0}", 
            (self.ADDITIONAL_SCOPES_TO_CONSENT_NAME, ", ".join(scopes))
        )
        return self
        
    def to_www_authenticate_header(self) -> str:
        """
        Creates a WWW-Authenticate header value for this exception,
        matching the C# AuthenticationService.AddBearerClaimToResponse logic.
        """
        header_parts = ["Bearer"]
        error_description = str(self.message_template).replace('\r', ' ').replace('\n', ' ')
        
        # Always include the authorization_uri for better client compatibility
        header_parts.append('authorization_uri="https://login.microsoftonline.com/common/oauth2/authorize"')

        if self._claims_for_conditional_access:
            header_parts.append(f'error="invalid_token"')
            header_parts.append(f'error_description="{error_description}"')
            header_parts.append(f'claims="{self._claims_for_conditional_access}"')
        elif self._additional_scopes_to_consent:
            scopes_str = " ".join(self._additional_scopes_to_consent)
            header_parts.append(f'error="insufficient_scope"')
            header_parts.append(f'error_description="{error_description}"')
            header_parts.append(f'scope="{scopes_str}"')
        else:
            header_parts.append(f'error="interaction_required"')
            header_parts.append(f'error_description="{error_description}"')

        return ", ".join(header_parts)

class TooManyRequestsException(WorkloadExceptionBase):
    """Exception for rate-limiting (429 Too Many Requests)."""
    
    def __init__(self, message: str = "Too many requests"):
        super().__init__(
            http_status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code=ErrorCodes.RateLimiting.TOO_MANY_REQUESTS,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.USER,
            is_permanent=False
        )

class InvalidItemPayloadException(WorkloadExceptionBase):
    """Exception for invalid item payloads."""
    
    def __init__(self, item_type: str, item_id: str):
        super().__init__(
            http_status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCodes.ItemPayload.INVALID_ITEM_PAYLOAD,
            message_template="{0} payload is invalid for id={1}. See MoreDetails for additional information.",
            message_parameters=[item_type, item_id],
            error_source=ErrorSource.USER,
            is_permanent=True
        )

class DoubledOperandsOverflowException(WorkloadExceptionBase):
    """Exception for overflow in doubled operands."""
    
    def __init__(self, message_parameters: List[str]):
        super().__init__(
            http_status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCodes.Item.DOUBLED_OPERANDS_OVERFLOW,
            message_template="{0} may lead to overflow",
            message_parameters=message_parameters,
            error_source=ErrorSource.USER,
            is_permanent=False
        )

class ItemMetadataNotFoundException(WorkloadExceptionBase):
    """Exception raised when an item's metadata cannot be found."""
    
    def __init__(self, item_object_id: UUID):
        super().__init__(
            http_status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCodes.Item.ITEM_METADATA_NOT_FOUND,
            message_template="Item metadata file cannot be found. It is advised to delete this item and create a new item instead (ItemId: {0})",
            message_parameters=[str(item_object_id)],
            error_source=ErrorSource.SYSTEM,
            is_permanent=True
        )

class InvalidParameterException(WorkloadExceptionBase):
    """Exception for invalid parameters."""
    
    def __init__(self, parameter_name: str, message: str):
        super().__init__(
            http_status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCodes.INVALID_PARAMETER,
            message_template="Invalid parameter '{0}': {1}",
            message_parameters=[parameter_name, message],
            error_source=ErrorSource.USER,
            is_permanent=True
        )

class KustoDataException(WorkloadExceptionBase):
    """Exception for Kusto data errors."""
    
    def __init__(self, message: str):
        super().__init__(
            http_status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCodes.Kusto.KUSTO_DATA_EXCEPTION,
            message_template=message,
            message_parameters=None,
            error_source=ErrorSource.USER,
            is_permanent=True
        )

class MissingLakehouseReferenceException(WorkloadExceptionBase):
    """Exception raised when a lakehouse reference is required but missing."""
    
    def __init__(self):
        super().__init__(
            http_status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCodes.INVALID_REQUEST,
            message_template="Missing lakehouse reference",
            message_parameters=None,
            error_source=ErrorSource.USER,
            is_permanent=True
        )
