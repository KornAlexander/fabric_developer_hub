from typing import Optional
from datetime import datetime, timezone
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class CommonItemMetadata(BaseModel):
    """
    Represents common metadata for Fabric items.
    """
    type: str = Field(..., description="The type of the item")
    tenant_object_id: UUID = Field(..., description="The tenant object ID")
    workspace_object_id: UUID = Field(..., description="The workspace object ID")
    item_object_id: UUID = Field(..., description="The item object ID")
    display_name: Optional[str] = Field(None, description="The display name of the item")
    description: Optional[str] = Field(None, description="The description of the item")
    last_updated_date_time_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="The UTC timestamp when the item was last updated"
    )
    
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )