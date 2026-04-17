
from pydantic import Field

from .item_reference import ItemReference


class FabricItem(ItemReference):
    """
    Model representing a Microsoft Fabric item.
    """
    type: str | None = Field(
        None,
        description="The type of the Fabric item"
    )
    display_name: str | None = Field(
        None,
        description="The display name of the Fabric item",
        alias="displayName"
    )
    description: str | None = Field(
        None,
        description="The description of the Fabric item"
    )
    workspace_name: str | None = Field(
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
                "displayName": "My Lakehouse",
                "description": "A lakehouse for storing data",
                "workspaceName": "My Workspace"
            }
        }
    }
