from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class OneLakePathData(BaseModel):
    """
    Model representing path data in OneLake storage.
    """
    name: str = Field(..., description="The name of the path")
    is_shortcut: bool = Field(False, description="Whether this path is a shortcut to another location", alias="isShortcut")
    account_type: Optional[str] = Field(None, description="The account type for the shortcut (e.g., 'ADLS')", alias="accountType") 
    is_directory: bool = Field(False, description="Whether this path represents a directory", alias="isDirectory")
    
    model_config = {
        "populate_by_name": True
    }

class OneLakePathContainer(BaseModel):
    """
    Container for OneLake paths.
    """
    paths: List[OneLakePathData] = Field(..., description="List of paths in the container")
    
    model_config = {
        "populate_by_name": True
    }

class OneLakeFolder(BaseModel):
    """
    Model representing a folder or file in OneLake storage.
    """
    name: str = Field(
        ...,
        description="The name of the folder or file"
    )
    is_directory: bool = Field(
        ...,
        description="Whether this path represents a directory",
        alias="isDirectory"
    )
    is_shortcut: Optional[bool] = Field(
        None,
        description="Whether this path is a shortcut to another location",
        alias="isShortcut"
    )
    account_type: Optional[str] = Field(
        None,
        description="The account type for the shortcut (e.g., 'ADLS')",
        alias="accountType"
    )
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "name": "data",
                "isDirectory": True,
                "isShortcut": False,
                "accountType": None
            }
        }
    }

class GetFoldersResult(BaseModel):
    """
    Model representing the result of a folder listing operation.
    """
    paths: List[OneLakeFolder] = Field(
        ...,
        description="List of folders and files in the requested directory"
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "paths": [
                    {
                        "name": "data",
                        "isDirectory": True,
                        "isShortcut": False
                    },
                    {
                        "name": "logs",
                        "isDirectory": True,
                        "isShortcut": False
                    }
                ]
            }
        }
    }