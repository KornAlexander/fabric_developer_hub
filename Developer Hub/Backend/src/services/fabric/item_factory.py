import logging

from app.core.service_registry import get_service_registry
from domain.constants.workload_constants import WorkloadConstants
from domain.exceptions.exceptions import UnexpectedItemTypeException
from domain.items.agenthub_item import AgentHubItem
from domain.items.base_item import ItemBase
from domain.models.authentication_models import AuthorizationContext

logger = logging.getLogger(__name__)

class ItemFactory:
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)


    def create_item(self, item_type: str, auth_context: AuthorizationContext) -> ItemBase:
        """Create an instance of the specified item type."""
        self.logger.info("Creating item of type %s", item_type)
        if item_type == WorkloadConstants.ItemTypes.AGENTHUB_ITEM:
            return AgentHubItem(auth_context)
        else:
            self.logger.error("Unexpected item type: %s", item_type)
            raise UnexpectedItemTypeException(f"Items of type {item_type} are not supported")


def get_item_factory() -> ItemFactory:
    """Get the singleton ItemFactory from ServiceRegistry."""
    try:
        return get_service_registry().get(ItemFactory)
    except KeyError:
        raise RuntimeError(
            "ItemFactory not initialized. Ensure ServiceInitializer.initialize_all_services() ran at startup."
        ) from None
