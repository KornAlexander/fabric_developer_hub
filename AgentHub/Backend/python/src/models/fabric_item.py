from typing import Optional
from pydantic import BaseModel, Field
from .item_reference import ItemReference

class FabricItem(ItemReference):
    """
    Model representing a Microsoft Fabric item.
    """
    type: Optional[str] = Field(
        None,
        description="The type of the Fabric item"
    )
    display_name: Optional[str] = Field(
        None,
        description="The display name of the Fabric item",
        alias="displayName"
    )
    description: Optional[str] = Field(
        None,
        description="The description of the Fabric item"
    )
    workspace_name: Optional[str] = Field(
        None,
        description="The name of the workspace containing this item",
        alias="workspaceName"
    )
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "id": "12345678-1234-5678-abcd-1234567890ab",
                "workspaceId": "98765432-1234-5678-abcd-1234567890ab",
                "type": "Lakehouse",
                "displayName": "Sample Lakehouse",
                "description": "A sample lakehouse for storing data",
                "workspaceName": "My Workspace"
            }
        }
    }