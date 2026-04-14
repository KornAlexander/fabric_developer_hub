from typing import Union, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class ItemReference(BaseModel):
    """
    A reference to an item in a workspace.
    
    Attributes:
        workspace_id: The ID of the workspace containing the item
        id: The ID of the item
    """
    workspace_id: Optional[Union[str, UUID]] = Field(
        default="00000000-0000-0000-0000-000000000000", 
        description="The ID of the workspace containing the item",
        alias="workspaceId"
    )
    id: Optional[Union[str, UUID]] = Field(
        default="00000000-0000-0000-0000-000000000000", 
        description="The ID of the item"
    )
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "workspaceId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
            }
        }
    }