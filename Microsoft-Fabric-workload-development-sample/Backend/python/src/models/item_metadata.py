from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict

from .common_item_metadata import CommonItemMetadata

# TypeVar for the generic type parameter
T = TypeVar('T')

class ItemMetadata(BaseModel, Generic[T]):
    """
    Model representing metadata for an item in Fabric.    
    Attributes:
        common_metadata: The common metadata shared by all items
        type_specific_metadata: Type-specific metadata that varies by item type
    """
    common_metadata: CommonItemMetadata
    type_specific_metadata: T
    
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )