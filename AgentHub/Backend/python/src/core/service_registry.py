import asyncio
import logging
from typing import Dict, List, Type, TypeVar, Optional, Callable, Any
from threading import Lock
import inspect

logger = logging.getLogger(__name__)

T = TypeVar('T')

class ServiceRegistry:
    """
    Thread-safe service registry for managing singleton instances.
    Supports both sync and async cleanup methods.
    """
    
    _instance: Optional['ServiceRegistry'] = None
    _lock = Lock()
    
    def __new__(cls) -> 'ServiceRegistry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Only initialize once
        if not hasattr(self, '_initialized_once'):
            self._services = {}
            self._factories = {}
            self._cleanup_handlers = []
            self._initialized = False
            self._is_cleaning_up = False
            self._initialized_once = True
    
    def register_factory(self, service_type: Type[T], factory: Callable[[], T]) -> None:
        """Register a factory function for lazy service creation."""
        self._factories[service_type] = factory
        logger.debug(f"Registered factory for {service_type.__name__}")
    
    def register(self, service_type: Type[T], instance: T) -> None:
        """Register a service instance directly."""
        self._services[service_type] = instance
        logger.debug(f"Registered instance for {service_type.__name__}")
        
        # Auto-register cleanup handlers in priority order
        if hasattr(instance, 'dispose_async') and callable(getattr(instance, 'dispose_async')):
            self._cleanup_handlers.append((service_type.__name__, instance))
        elif hasattr(instance, 'close') and callable(getattr(instance, 'close')):
            self._cleanup_handlers.append((service_type.__name__, instance))
    
    def get(self, service_type: Type[T]) -> T:
        """Get a service instance. Creates it using factory if not exists."""
        if service_type in self._services:
            return self._services[service_type]
        
        if service_type in self._factories:
            instance = self._factories[service_type]()
            self._services[service_type] = instance
            logger.info(f"Created service instance: {service_type.__name__}")
            # Auto-register cleanup
            if hasattr(instance, 'dispose_async') and callable(getattr(instance, 'dispose_async')):
                self._cleanup_handlers.append((service_type.__name__, instance))
            elif hasattr(instance, 'close') and callable(getattr(instance, 'close')):
                self._cleanup_handlers.append((service_type.__name__, instance))

            return instance
        
        raise KeyError(f"Service not registered: {service_type.__name__}")
    
    def has(self, service_type: Type[T]) -> bool:
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
                
            logger.info(f"Starting cleanup of {len(self._cleanup_handlers)} services...")
            
            # Process in reverse order (LIFO)
            for service_name, instance in reversed(self._cleanup_handlers):
                try:
                    # Check for dispose_async first (preferred pattern)
                    if hasattr(instance, 'dispose_async'):
                        dispose_method = getattr(instance, 'dispose_async')
                        if inspect.iscoroutinefunction(dispose_method):
                            try:
                                await dispose_method()
                                logger.debug(f"Disposed {service_name} using dispose_async")
                                continue
                            except RuntimeError as e:
                                if "no running event loop" in str(e):
                                    logger.warning(f"No event loop for {service_name}, skipping async cleanup")
                                    continue
                                raise
                    
                    # Fallback to close method
                    if hasattr(instance, 'close'):
                        close_method = getattr(instance, 'close')
                        if inspect.iscoroutinefunction(close_method):
                            try:
                                await close_method()
                                logger.debug(f"Cleaned up {service_name} using async close")
                            except RuntimeError as e:
                                if "no running event loop" in str(e):
                                    logger.warning(f"No event loop for {service_name}, trying sync close")
                                    continue
                                raise
                        else:
                            # Sync close method
                            close_method()
                            logger.debug(f"Cleaned up {service_name} using sync close")

                except Exception as e:
                    logger.error(f"Error cleaning up {service_name}: {e}", exc_info=True)
                    # Continue with other services even if one fails
            
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
                    if hasattr(instance, 'close'):
                        close_method = getattr(instance, 'close')
                        if not inspect.iscoroutinefunction(close_method):
                            close_method()
                            logger.debug(f"Sync cleanup of {service_name}")
                except Exception as e:
                    logger.error(f"Error in sync cleanup of {service_name}: {e}")
        except Exception as e:
            logger.error(f"Error during sync cleanup: {e}")
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
    
    def get_all_services(self) -> List[str]:
        """Get list of all registered service names."""
        return [svc.__name__ for svc in self._services.keys()]

def get_service_registry() -> ServiceRegistry:
    """Get the singleton ServiceRegistry instance."""
    return ServiceRegistry()