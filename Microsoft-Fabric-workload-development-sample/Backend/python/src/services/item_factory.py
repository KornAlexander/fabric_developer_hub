import logging
from typing import Dict, Type
from models.authentication_models import AuthorizationContext
from items.base_item import ItemBase
from items.item1 import Item1
from constants.workload_constants import WorkloadConstants
from exceptions.exceptions import UnexpectedItemTypeException

logger = logging.getLogger(__name__)

class ItemFactory:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
    
    def create_item(self, item_type: str, auth_context: AuthorizationContext) -> ItemBase:
        """Create an instance of the specified item type."""
        self.logger.info(f"Creating item of type {item_type}")
        if item_type == WorkloadConstants.ItemTypes.ITEM1:
            return Item1(auth_context)
        else:
            self.logger.error(f"Unexpected item type: {item_type}")
            raise UnexpectedItemTypeException(f"Items of type {item_type} are not supported")
        

def get_item_factory() -> ItemFactory:
    # Use a singleton pattern for consistency
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    
    if not registry.has(ItemFactory):
        if not hasattr(get_item_factory, "instance"):
            get_item_factory.instance = ItemFactory()
        return get_item_factory.instance
    
    return registry.get(ItemFactory)