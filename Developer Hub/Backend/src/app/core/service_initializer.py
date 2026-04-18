import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.core.service_registry import get_service_registry
from services.auth.authentication import AuthenticationService
from services.auth.authorization import AuthorizationHandler
from services.auth.open_id_connect_configuration import (
    OpenIdConnectConfigurationManager,
    get_openid_manager_service,
)
from services.configuration_service import get_configuration_service
from services.fabric.item_factory import ItemFactory
from services.fabric.item_metadata_store import ItemMetadataStore
from services.fabric.lakehouse_client_service import LakehouseClientService
from services.fabric.onelake_client_service import OneLakeClientService
from services.http_client import HttpClientService

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _gather_or_raise_first(label: str, *coros: Awaitable[None]) -> None:
    """Run independent service-init coros in parallel and re-raise the first
    failure with chained traceback.

    `asyncio.gather(..., return_exceptions=True)` swallows exceptions into the
    result list; we re-raise the first one with `raise exc from exc` so the
    original traceback (and tenant-isolation / token-validation context) is
    preserved when bootstrap fails.
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.error("Failed to initialize %s service at index %d: %s", label, i, result)
            raise result


class ServiceInitializer:
    """Handles initialization of all application services with optimized parallel loading."""

    def __init__(self) -> None:
        self.registry = get_service_registry()
        self._initialization_lock = asyncio.Lock()

    async def initialize_all_services(self) -> None:
        """
        Initialize all services with parallel execution where possible.
        This should be called once at application startup.
        """
        async with self._initialization_lock:
            if self.registry.is_initialized:
                logger.warning("Services already initialized, skipping...")
                return

            logger.info("Starting service initialization...")

            try:
                # 0. Initialize ConfigurationService first (all other services
                #    may depend on it).
                config_service = get_configuration_service()
                logger.info(
                    "Configuration loaded for environment: %s",
                    config_service.get_environment(),
                )

                # 1. Initialize services with no dependencies in parallel.
                logger.info("Initializing independent services...")
                await _gather_or_raise_first(
                    "independent",
                    self._initialize_openid_manager(),
                    self._initialize_http_client(),
                    self._initialize_item_metadata_store(),
                )

                # 2. Initialize services that depend on OpenID manager.
                await self._initialize_authentication_service()

                # 3. Initialize remaining services in parallel.
                logger.info("Initializing dependent services...")
                await _gather_or_raise_first(
                    "dependent",
                    self._initialize_authorization_handler(),
                    self._initialize_item_factory(),
                    self._initialize_lakehouse_client(),
                    self._initialize_onelake_client(),
                )

                self.registry.mark_initialized()
                logger.info("All services initialized successfully!")

            except Exception:
                # Tear down anything partially constructed so a retry sees a
                # clean registry. logger.exception captures the traceback.
                logger.exception("Failed to initialize services")
                self.registry.clear()
                raise

    async def _initialize_openid_manager(self) -> None:
        """Initialize OpenID Connect Configuration Manager."""
        logger.info("Initializing OpenID Connect Configuration Manager...")
        openid_manager = await get_openid_manager_service()
        self.registry.register(OpenIdConnectConfigurationManager, openid_manager)

    async def _initialize_http_client(self) -> None:
        """Initialize HTTP Client Service."""
        logger.info("Initializing HTTP Client Service...")
        http_client = HttpClientService()
        self.registry.register(HttpClientService, http_client)

    async def _initialize_item_metadata_store(self) -> None:
        """Initialize Item Metadata Store."""
        logger.info("Initializing Item Metadata Store...")
        metadata_store = ItemMetadataStore()
        self.registry.register(ItemMetadataStore, metadata_store)

    async def _initialize_authentication_service(self) -> None:
        """Initialize Authentication Service."""
        logger.info("Initializing Authentication Service...")
        openid_manager = self.registry.get(OpenIdConnectConfigurationManager)
        auth_service = AuthenticationService(openid_manager=openid_manager)
        self.registry.register(AuthenticationService, auth_service)

    async def _initialize_authorization_handler(self) -> None:
        """Initialize Authorization Handler."""
        logger.info("Initializing Authorization Handler...")
        auth_handler = AuthorizationHandler()
        self.registry.register(AuthorizationHandler, auth_handler)

    async def _initialize_item_factory(self) -> None:
        """Initialize Item Factory."""
        logger.info("Initializing Item Factory...")
        item_factory = ItemFactory()
        self.registry.register(ItemFactory, item_factory)

    async def _initialize_lakehouse_client(self) -> None:
        """Initialize Lakehouse Client Service."""
        logger.info("Initializing Lakehouse Client Service...")
        lakehouse_client = LakehouseClientService()
        self.registry.register(LakehouseClientService, lakehouse_client)

    async def _initialize_onelake_client(self) -> None:
        """Initialize OneLake Client Service."""
        logger.info("Initializing OneLake Client Service...")
        onelake_client = OneLakeClientService()
        self.registry.register(OneLakeClientService, onelake_client)


def get_service_initializer() -> ServiceInitializer:
    """Get the singleton ServiceInitializer instance."""
    registry = get_service_registry()
    if not registry.has(ServiceInitializer):
        initializer = ServiceInitializer()
        registry.register(ServiceInitializer, initializer)
        logger.debug("ServiceInitializer registered in ServiceRegistry")
    return registry.get(ServiceInitializer)
