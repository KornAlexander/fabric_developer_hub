from typing import Optional
from pydantic import BaseModel, Field


class LakehouseFile(BaseModel):
    """Model representing a file in a Lakehouse."""
    
    name: str = Field(
        ...,
        description="The name of the file"
    )
    path: str = Field(
        ...,
        description="The relative path of the file within the Files directory"
    )
    is_directory: bool = Field(
        ...,
        description="Whether this path represents a directory"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "data.csv",
                "path": "subfolder/data.csv",
                "is_directory": False
            }
        }
    }