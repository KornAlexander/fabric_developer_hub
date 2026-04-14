from typing import Optional
from pydantic import BaseModel, Field, field_validator
import uuid

class WriteToLakehouseFileRequest(BaseModel):
    """
    Request model for writing content to a Lakehouse file.
    """
    workspace_id: str = Field(
        ..., 
        description="The workspace ID containing the lakehouse",
        examples=["12345678-1234-5678-abcd-1234567890ab"]
    )
    lakehouse_id: str = Field(
        ..., 
        description="The lakehouse ID where the file will be stored",
        examples=["12345678-1234-5678-abcd-1234567890ab"]
    )
    file_name: str = Field(
        ..., 
        description="Name of the file to be written",
        examples=["data.json"]
    )
    content: str = Field(
        ..., 
        description="Content to write to the file"
    )
    overwrite_if_exists: bool = Field(
        False, 
        description="Whether to overwrite the file if it already exists"
    )
    
    # V2-style validators to ensure workspace_id and lakehouse_id are valid UUIDs
    @field_validator('workspace_id', 'lakehouse_id')
    @classmethod  # Field validators should be classmethods in V2
    def validate_uuid(cls, v):
        try:
            uuid.UUID(v)
            return v
        except ValueError:
            raise ValueError(f"Invalid UUID format: {v}")
    
    model_config = {  # Use model_config instead of Config in V2
        "json_schema_extra": {
            "example": {
                "workspace_id": "12345678-1234-5678-abcd-1234567890ab",
                "lakehouse_id": "98765432-1234-5678-abcd-1234567890ab",
                "file_name": "sample-data.json",
                "content": "{ \"key\": \"value\" }",
                "overwrite_if_exists": True
            }
        }
    }