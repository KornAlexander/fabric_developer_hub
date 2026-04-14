from typing import List, Optional, Dict, Any, Tuple
from fastapi import status
from fastapi.responses import JSONResponse
from fabric_api.models.error_source import ErrorSource
from fabric_api.models.error_response import ErrorResponse
from fabric_api.models.error_extended_information import ErrorExtendedInformation
from fabric_api.models.name_value_pair import NameValuePair

class WorkloadExceptionBase(Exception):
    """Base class for workload exceptions."""
    
    def __init__(
        self,
        http_status_code: int,
        error_code: str,
        message_template: str,
        message_parameters: Optional[List[str]] = None,
        error_source: ErrorSource = ErrorSource.SYSTEM,
        is_permanent: bool = False
    ):
        self.http_status_code = http_status_code
        self.error_code = error_code
        self.message_template = message_template
        self.message_parameters = message_parameters or []
        self.error_source = error_source
        self.is_permanent = is_permanent
        self.details: List[ErrorExtendedInformation] = []
        
        # Format the message with parameters
        if message_parameters:
            formatted_message = message_template.format(*message_parameters)
        else:
            formatted_message = message_template
            
        super().__init__(formatted_message)
    
    def with_detail(self, error_code: str, message_template: str, *parameters: Tuple[str, str]) -> 'WorkloadExceptionBase':
        """Add detailed error information."""
        parameter_values = [p[1] for p in parameters]
        
        detail = ErrorExtendedInformation(
            error_code=error_code,
            message=message_template.format(*parameter_values),
            message_parameters=parameter_values,
            additional_parameters=[NameValuePair(name=p[0], value=p[1]) for p in parameters]
        )
        
        self.details.append(detail)
        return self
        
    def to_response(self) -> JSONResponse:
        """Convert exception to FastAPI JSONResponse."""
        response = ErrorResponse(
            error_code=self.error_code,
            message=str(self),
            message_parameters=self.message_parameters if self.message_parameters else None,
            source=self.error_source,
            is_permanent=self.is_permanent,
            more_details=self.details if self.details else None
        )
        
        return JSONResponse(
            status_code=self.http_status_code,
            content=response.model_dump(exclude_none=True)
        )
        
    def to_telemetry_string(self) -> str:
        """Convert to string for telemetry purposes."""
        return str(self)