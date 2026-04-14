from typing import Dict, List, Optional, ClassVar, Dict, Any
import re
from enum import IntEnum
from exceptions.exceptions import AuthenticationException
from pydantic import BaseModel, Field, computed_field, ConfigDict

class Claim(BaseModel):
    """
    Represents an identity claim.
    """
    type: str = Field(..., description="The claim type")
    value: Any = Field(..., description="The claim value")
    
    model_config = ConfigDict(
        arbitrary_types_allowed=True)

class AuthorizationContext(BaseModel):
    """Context containing information about an authenticated request."""
    original_subject_token: Optional[str] = None
    tenant_object_id: Optional[str] = None
    claims: List[Claim] = Field(default_factory=list)
    
    @property
    def has_subject_context(self) -> bool:
        """Gets a value indicating whether there is subject context."""
        return self.original_subject_token is not None and len(self.original_subject_token) > 0
    
    @property
    def object_id(self) -> Optional[str]:
        """Gets the object ID from the claims."""
        for claim in self.claims:
            if claim.type == "oid":
                return claim.value
        return None
    
class TokenVersion(IntEnum):
    """Token version enumeration"""
    V1 = 1
    V2 = 2

class SubjectAndAppToken(BaseModel):
    """Container for subject and app tokens."""
    HEADER_PATTERN: ClassVar[str] = r'^SubjectAndAppToken1\.0 subjectToken="(eyJ[\w\-\._]+)", appToken="(eyJ[\w\-\._]+)"$'
    HEADER_PATTERN_EMPTY_SUBJECT: ClassVar[str] = r'^SubjectAndAppToken1\.0 subjectToken="", appToken="(eyJ[\w\-\._]+)"$'
    subject_token: Optional[str] = None
    app_token: str
    
    @classmethod
    def parse(cls, auth_header_value: str) -> 'SubjectAndAppToken':
        """Parse the SubjectAndAppToken from the authorization header."""
        if not auth_header_value:
            raise AuthenticationException("Invalid Authorization header")
        
        # First, try matching the pattern with a non-empty subject token
        match = re.fullmatch(cls.HEADER_PATTERN, auth_header_value)
        if match:
            subject_token = match.group(1)
            app_token = match.group(2)
            return cls(subject_token=subject_token, app_token=app_token)
        
        # If no match, try matching the pattern with an empty subject token
        match_empty_subject = re.fullmatch(cls.HEADER_PATTERN_EMPTY_SUBJECT, auth_header_value)
        if match_empty_subject:
            app_token = match_empty_subject.group(1)
            return cls(subject_token=None, app_token=app_token)
        
        # If no match, raise an exception
        raise AuthenticationException("Invalid SubjectAndAppToken header format")
    
    @staticmethod
    def generate_authorization_header_value(subject_token: Optional[str], app_token: str) -> str:
        """Generates the string value for the Authorization header with SubjectAndAppToken1.0 scheme."""
        # Ensure subject_token is an empty string if None, to match C# behavior where subjectToken=""
        effective_subject_token = subject_token if subject_token is not None else ""
        return f'SubjectAndAppToken1.0 subjectToken="{effective_subject_token}", appToken="{app_token}"'
