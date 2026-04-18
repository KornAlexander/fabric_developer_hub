import inspect
import logging
from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ServiceRegistry:
    """
    Thread-safe service registry for managing singleton instances.
    Supports both sync and async cleanup methods.
    """

    _instance: "ServiceRegistry | None" = None
    _lock = Lock()

    def __new__(cls) -> "ServiceRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Only initialize once. The singleton's __new__ may return the same
        # instance many times — without this guard each call would wipe state.
        if not hasattr(self, "_initialized_once"):
            self._services: dict[type[Any], Any] = {}
            self._factories: dict[type[Any], Callable[[], Any]] = {}
            self._cleanup_handlers: list[tuple[str, Any]] = []
            self._initialized = False
            self._is_cleaning_up = False
            self._initialized_once = True

    def register_factory(self, service_type: type[T], factory: Callable[[], T]) -> None:
        """Register a factory function for lazy service creation."""
        self._factories[service_type] = factory
        logger.debug("Registered factory for %s", service_type.__name__)

    def register(self, service_type: type[T], instance: T) -> None:
        """Register a service instance directly."""
        self._services[service_type] = instance
        logger.debug("Registered instance for %s", service_type.__name__)

        # Auto-register cleanup handlers in priority order
        if hasattr(instance, "dispose_async") and callable(instance.dispose_async):
            self._cleanup_handlers.append((service_type.__name__, instance))
        elif hasattr(instance, "close") and callable(instance.close):
            self._cleanup_handlers.append((service_type.__name__, instance))

    def get(self, service_type: type[T]) -> T:
        """Get a service instance. Creates it using factory if not exists."""
        if service_type in self._services:
            return self._services[service_type]

        if service_type in self._factories:
            instance = self._factories[service_type]()
            self._services[service_type] = instance
            logger.info("Created service instance: %s", service_type.__name__)
            # Auto-register cleanup
            if hasattr(instance, "dispose_async") and callable(instance.dispose_async):
                self._cleanup_handlers.append((service_type.__name__, instance))
            elif hasattr(instance, "close") and callable(instance.close):
                self._cleanup_handlers.append((service_type.__name__, instance))

            return instance

        raise KeyError(f"Service not registered: {service_type.__name__}")

    def has(self, service_type: type[T]) -> bool:
        """Check if a service is registered."""
        return service_type in self._services or service_type in self._factories

    async def cleanup(self) -> None:
        """
        Cleanup all registered services that have cleanup methods.
        Properly handles both sync and async cleanup methods.
        """
        if self._is_cleaning_up:
            logger.debug("Cleanup already in progress, skipping...")
            return

        self._is_cleaning_up = True
        try:
            if not self._cleanup_handlers:
                logger.info("No services to cleanup")
                return

            logger.info("Starting cleanup of %d services...", len(self._cleanup_handlers))

            # Process in reverse order (LIFO)
            for service_name, instance in reversed(self._cleanup_handlers):
                try:
                    # Check for dispose_async first (preferred pattern)
                    if hasattr(instance, "dispose_async"):
                        dispose_method = instance.dispose_async
                        if inspect.iscoroutinefunction(dispose_method):
                            try:
                                await dispose_method()
                                logger.debug("Disposed %s using dispose_async", service_name)
                                continue
                            except RuntimeError as e:
                                if "no running event loop" in str(e):
                                    logger.warning(
                                        "No event loop for %s, skipping async cleanup",
                                        service_name,
                                    )
                                    continue
                                raise

                    # Fallback to close method
                    if hasattr(instance, "close"):
                        close_method = instance.close
                        if inspect.iscoroutinefunction(close_method):
                            try:
                                await close_method()
                                logger.debug("Cleaned up %s using async close", service_name)
                            except RuntimeError as e:
                                if "no running event loop" in str(e):
                                    logger.warning(
                                        "No event loop for %s, trying sync close",
                                        service_name,
                                    )
                                    continue
                                raise
                        else:
                            # Sync close method
                            close_method()
                            logger.debug("Cleaned up %s using sync close", service_name)

                except Exception:
                    # Cleanup must never raise — keep tearing down the rest of
                    # the services on best-effort basis. Log with traceback so
                    # ops can still see what happened.
                    logger.error("Error cleaning up %s", service_name, exc_info=True)

            logger.info("Service cleanup complete")

        finally:
            # Always clear the state, even if cleanup failed
            self._cleanup_handlers.clear()
            self._services.clear()
            self._initialized = False
            self._is_cleaning_up = False

    def clear(self) -> None:
        """Clear all registered services synchronously (for emergency cleanup)."""
        try:
            # Try to cleanup sync services first
            for service_name, instance in reversed(self._cleanup_handlers):
                try:
                    if hasattr(instance, "close"):
                        close_method = instance.close
                        if not inspect.iscoroutinefunction(close_method):
                            close_method()
                            logger.debug("Sync cleanup of %s", service_name)
                except Exception:
                    logger.error("Error in sync cleanup of %s", service_name, exc_info=True)
        except Exception:
            logger.error("Error during sync cleanup", exc_info=True)
        finally:
            # Always clear the registry
            self._services.clear()
            self._factories.clear()
            self._cleanup_handlers.clear()
            self._initialized = False
            self._is_cleaning_up = False
            logger.info("Service registry cleared")

    @property
    def is_initialized(self) -> bool:
        """Check if the registry has been initialized."""
        return self._initialized

    def mark_initialized(self) -> None:
        """Mark the registry as initialized."""
        self._initialized = True

    def get_all_services(self) -> list[str]:
        """Get list of all registered service names."""
        return [svc.__name__ for svc in self._services.keys()]


def get_service_registry() -> ServiceRegistry:
    """Get the singleton ServiceRegistry instance."""
    return ServiceRegistry()
