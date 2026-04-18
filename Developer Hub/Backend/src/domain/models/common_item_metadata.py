from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommonItemMetadata(BaseModel):
    """
    Represents common metadata for Fabric items.
    """
    type: str = Field(..., description="The type of the item")
    tenant_object_id: UUID = Field(..., description="The tenant object ID")
    workspace_object_id: UUID = Field(..., description="The workspace object ID")
    item_object_id: UUID = Field(..., description="The item object ID")
    display_name: str | None = Field(None, description="The display name of the item")
    description: str | None = Field(None, description="The description of the item")
    last_updated_date_time_utc: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="The UTC timestamp when the item was last updated"
    )

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
