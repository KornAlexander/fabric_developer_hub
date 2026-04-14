import asyncio
import os
import logging
import logging.config
import sys
import time
from typing import Optional, Set
import uuid
from contextlib import asynccontextmanager
from fabric_api.impl.jobs_controller import cleanup_background_tasks
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from services.configuration_service import get_configuration_service
from core.service_initializer import get_service_initializer
from core.service_registry import get_service_registry

# Import controllers
from fabric_api.apis.endpoint_resolution_api import router as EndpointResolutionApiRouter
from fabric_api.apis.item_lifecycle_api import router as ItemLifecycleApiRouter
from fabric_api.apis.jobs_api import router as JobsApiRouter
from impl.fabric_extension_controller import router as fabric_extension_router
from impl.onelake_controller import router as onelake_controller
from impl.lakehouse_controller import router as lakehouse_controller
from impl.github_chat_controller import router as github_chat_router, set_mcp_manager, _get_copilot_token, _acquire_mcp_tokens
from impl.agenthub_controller import router as agenthub_router
from services.mcp_client_manager import MCPClientManager
from services import job_store as agenthub_store
from services import orchestrator_engine

from middleware.exception_handlers import register_exception_handlers

def setup_logging(config_service=None) -> logging.Logger:
    """Setup logging configuration based on settings."""
    if config_service is None:
        config_service = get_configuration_service()
    
    # Map configuration log level to Python log level
    log_level_mapping = {
        "Trace": "DEBUG",
        "Debug": "DEBUG", 
        "Information": "INFO",
        "Warning": "WARNING",
        "Error": "ERROR",
        "Critical": "CRITICAL",
        "None": "CRITICAL"
    }

    config_log_level = config_service.get_log_level()
    log_level = log_level_mapping.get(config_log_level, "INFO")

    # Get user's AppData/Roaming directory (cross-platform)
    appdata = Path.home() / '.config' / 'fabric_backend'
    if os.name == 'nt':
    # On Windows, use APPDATA environment variable (Roaming)
        appdata = os.environ.get('APPDATA')
        if not appdata:
            # Fallback if APPDATA is not set
            appdata = os.path.expanduser('~\\AppData\\Roaming')

    # Create logs directory
    app_name = config_service.get_app_name().replace(" ", "_")
    log_dir = Path(appdata) / app_name / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Log file path with date rotation
    log_filename = f'fabric_backend_{datetime.now().strftime("%Y%m%d")}.log'
    log_file = log_dir / log_filename

    # Logging configuration
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s() - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "level": log_level,
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "detailed",
                "filename": str(log_file),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
                "level": "INFO",
                "encoding": "utf-8"
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["console", "file"]
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False
            },
            "uvicorn.error": {
                "handlers": ["console"],
                "propagate": False,
                "level": "INFO"
            },
            "uvicorn.access": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False
            },
            "httpx": {
                "level": "WARNING"
            },
            "httpcore": {
                "level": "WARNING"
            },
            "asyncio": {
                "level": "WARNING"
            }
        }
    }

    logging.config.dictConfig(logging_config)
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized - Level: {log_level}, File: {log_file}")
    
    return logger

# Global state for shutdown handling
class ApplicationState:
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False
        self.logger: Optional[logging.Logger] = None
        self.active_requests: Set[str] = set()
        self.request_lock = asyncio.Lock()

app_state = ApplicationState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifecycle with proper startup and shutdown."""
    # Startup
    startup_start = time.time()
    
    # Get configuration service (will create if not exists)
    config_service = get_configuration_service()
    
    # Setup logging with configuration
    logger = setup_logging(config_service)
    app_state.logger = logger
    
    logger.info("=" * 60)
    logger.info(f"Starting {config_service.get_app_name()}...")
    logger.info(f"Environment: {config_service.get_environment()}")
    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Platform: {sys.platform}")
    logger.info(f"Process ID: {os.getpid()}")
    logger.info("=" * 60)

    logger.info("Configuration Summary:")
    logger.info(f"  - Host: {config_service.get_host()}")
    logger.info(f"  - Port: {config_service.get_port()}")
    logger.info(f"  - Debug: {config_service.is_debug()}")
    logger.info(f"  - Log Level: {config_service.get_log_level()}")
    logger.info(f"  - Shutdown Timeout: {config_service.get_shutdown_timeout()}s")
    
    try:
        # Initialize all services with parallel execution
        initializer = get_service_initializer()
        await initializer.initialize_all_services()

        # Initialize MCP client manager for agentic chat
        try:
            mcp_config_path = os.path.join(os.path.dirname(__file__), "mcp_servers.json")
            mcp_manager = MCPClientManager(mcp_config_path)
            await mcp_manager.discover_tools()
            set_mcp_manager(mcp_manager)
            tool_count = len(mcp_manager.tools)
            logger.info(f"✓ MCP client initialized: {tool_count} tools from {len(mcp_manager.config.get('servers', {}))} servers")
        except Exception as e:
            logger.warning(f"⚠ MCP initialization failed (chat will work without tools): {e}")
            mcp_manager = None

        # Initialize AgentHub database
        try:
            agenthub_store.init_db()
            logger.info("✓ AgentHub database initialized")
        except Exception as e:
            logger.warning(f"⚠ AgentHub DB init failed: {e}")

        # Configure orchestrator engine
        if mcp_manager:
            orchestrator_engine.configure(mcp_manager, _get_copilot_token, _acquire_mcp_tokens)
            logger.info("✓ Orchestrator engine configured")
        
        startup_time = time.time() - startup_start
        logger.info(f"✓ Application started successfully in {startup_time:.2f}s")
        logger.info(f"✓ Server: {config_service.get_http_endpoint()}")
        logger.info(f"✓ Debug Mode: {config_service.is_debug()}")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"Failed to start application: {str(e)}", exc_info=True)
        raise
        
    yield
    
    # Shutdown
    shutdown_start_time = time.time()
    logger.info("=" * 60)
    logger.info("Application shutdown initiated...")
    # Mark as shutting down
    app_state.is_shutting_down = True
    app_state.shutdown_event.set()

    # Get shutdown timeout and allocate time proportionally
    total_timeout = config_service.get_shutdown_timeout()
    tasks_cleanup_timeout = total_timeout * 0.6  # 60% for background tasks
    service_cleanup_timeout = total_timeout * 0.3  # 30% for services
    
     # 1. Clean up background tasks
    try:
        logger.info(f"Cleaning up background tasks (timeout: {tasks_cleanup_timeout:.1f}s)...")
        await cleanup_background_tasks(timeout=tasks_cleanup_timeout)
        logger.info("✓ Background tasks cleanup completed")
    except Exception as e:
        logger.error(f"Error during background tasks cleanup: {str(e)}", exc_info=True)

    # 2. Clean up services
    try:
        registry = get_service_registry()
        logger.info(f"Cleaning up services (timeout: {service_cleanup_timeout:.1f}s)...")
        await asyncio.wait_for(registry.cleanup(), timeout=service_cleanup_timeout)
        logger.info("✓ Service registry cleanup completed")
    except asyncio.TimeoutError:
        logger.warning("⚠ Service registry cleanup timed out")
    except Exception as e:
        logger.error(f"Error during service registry cleanup: {str(e)}", exc_info=True)
        
    shutdown_duration = time.time() - shutdown_start_time
    logger.info(f"✓ Application shutdown completed in {shutdown_duration:.2f}s")
    logger.info("=" * 60)

# Create FastAPI app
def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config_service = get_configuration_service()
    
    app = FastAPI(
        title=config_service.get_app_name(),
        description="Python implementation of Microsoft Fabric backend sample workload",
        version="1.0.0",
        root_path="/workload",
        lifespan=lifespan,
        docs_url="/api/docs" if config_service.is_debug() else None,
        redoc_url="/api/redoc" if config_service.is_debug() else None,
        openapi_url="/api/openapi.json" if config_service.is_debug() else None
    )
    
    # Configure middleware
    
    # Security middleware (only in production)
    if config_service.is_production():
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=config_service.get_allowed_hosts()
        )
    
    # Compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config_service.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"]
    )
    
    # Register exception handlers
    register_exception_handlers(app)
    
    # Include routers with proper prefixes
    app.include_router(EndpointResolutionApiRouter)
    app.include_router(ItemLifecycleApiRouter)
    app.include_router(JobsApiRouter)
    app.include_router(fabric_extension_router)
    app.include_router(onelake_controller)
    app.include_router(lakehouse_controller)
    app.include_router(github_chat_router)
    app.include_router(agenthub_router)
    
    return app

# Create app instance
app = create_app()

@app.get("/health", tags=["monitoring"])
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": app.version,
        "environment": os.environ.get('PYTHON_ENVIRONMENT', 'Development')
    }

@app.get("/ready", tags=["monitoring"])
async def readiness_check():
    """Readiness check for Kubernetes and load balancers."""
    try:
        registry = get_service_registry()
        
        if not registry.is_initialized:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not ready",
                    "error": "Services not initialized",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
        
        return {
            "status": "ready",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": registry.get_all_services()
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not ready",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add request processing time and request ID headers."""
    # Check if shutting down
    if app_state.is_shutting_down:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"message": "Server is shutting down"}
        )
    
    # Generate or get request ID
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    
    # Track active request
    async with app_state.request_lock:
        app_state.active_requests.add(request_id)
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Add headers
        response.headers["X-Process-Time"] = f"{process_time:.3f}"
        response.headers["X-Request-ID"] = request_id
        
        # Log request (skip health checks to reduce noise)
        if request.url.path not in ["/health", "/ready"] and app_state.logger:
            app_state.logger.info(
                f"{request.method} {request.url.path} → {response.status_code} "
                f"({process_time:.3f}s) [ID: {request_id[:8]}]"
            )
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        if app_state.logger:
            app_state.logger.error(
                f"{request.method} {request.url.path} → ERROR "
                f"({process_time:.3f}s) [ID: {request_id[:8]}]: {str(e)}",
                exc_info=True
            )
        raise
    finally:
        # Remove from active requests
        async with app_state.request_lock:
            app_state.active_requests.discard(request_id)

def main():
    """Main entry point for the application."""
    # Get configuration first
    config_service = get_configuration_service()

    uvicorn.run(
        "main:app",
        host=config_service.get_host(),
        port=config_service.get_port(),
        reload=not config_service.is_production(),
        reload_dirs=[os.path.dirname(__file__)] if not config_service.is_production() else None,
        workers=config_service.get_workers() if config_service.is_production() else None,
        loop="asyncio",
        log_config=None,
        access_log=False,
        limit_concurrency=1000,
        limit_max_requests=10000 if config_service.is_production() else None,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=max(config_service.get_shutdown_timeout() + 10, 30),
        lifespan="on",
        # SSL configuration
        ssl_keyfile=os.environ.get("SSL_KEYFILE") if config_service.is_production() else None,
        ssl_certfile=os.environ.get("SSL_CERTFILE") if config_service.is_production() else None,
    )

if __name__ == "__main__":
    main()