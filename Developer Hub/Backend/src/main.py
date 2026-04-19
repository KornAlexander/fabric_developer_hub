import app.bootstrap as _bootstrap  # noqa: F401  # MUST be first import: loads .env before anything reads os.environ

import asyncio
import logging
import logging.config
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.service_initializer import get_service_initializer
from app.core.service_registry import get_service_registry

# Import controllers
from fabric_api.apis.endpoint_resolution_api import (
    router as EndpointResolutionApiRouter,
)
from fabric_api.apis.item_lifecycle_api import router as ItemLifecycleApiRouter
from fabric_api.apis.jobs_api import router as JobsApiRouter
from fabric_api.impl.jobs_controller import cleanup_background_tasks
from api.agenthub_controller import router as agenthub_router
from api.github_chat_controller import (
    _acquire_mcp_tokens,
    _get_copilot_token,
    set_mcp_manager,
)
from api.github_chat_controller import router as github_chat_router
from api.lakehouse_controller import router as lakehouse_controller
from api.onelake_controller import router as onelake_controller
from app.exception_handlers import register_exception_handlers
from services.agenthub import session_store as agenthub_store
from services.agenthub.orchestrator_engine import get_orchestrator_engine
from services.configuration_service import get_configuration_service
from services.mcp.mcp_client_manager import MCPClientManager


class ColorFormatter(logging.Formatter):
    """Console formatter that colorises ``%(levelname)s`` with ANSI codes.

    Enabled by default on TTY-capable stdout (Docker Compose attaches one).
    The ``NO_COLOR`` environment variable (https://no-color.org) disables
    colour. File handlers keep the plain formatter so log files stay
    grep-friendly.
    """

    _COLORS = {
        "DEBUG": "\033[38;5;244m",   # grey
        "INFO": "\033[38;5;39m",      # cyan/blue
        "WARNING": "\033[38;5;214m",  # orange
        "ERROR": "\033[1;31m",        # bold red
        "CRITICAL": "\033[1;97;41m",  # white on red
    }
    _RESET = "\033[0m"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enabled = (
            os.environ.get("NO_COLOR") is None
            and (sys.stdout.isatty() or os.environ.get("FORCE_COLOR"))
        )

    def format(self, record: logging.LogRecord) -> str:
        if not self._enabled:
            return super().format(record)
        colour = self._COLORS.get(record.levelname)
        if colour:
            original = record.levelname
            record.levelname = f"{colour}{original}{self._RESET}"
            try:
                return super().format(record)
            finally:
                record.levelname = original
        return super().format(record)


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

    # Get user's AppData/Roaming directory (cross-platform).
    # Path() coerces a str (the Windows APPDATA env var) the same as a Path.
    appdata: Path
    if os.name == 'nt':
        # On Windows, use APPDATA environment variable (Roaming)
        appdata_env = os.environ.get('APPDATA') or os.path.expanduser('~\\AppData\\Roaming')
        appdata = Path(appdata_env)
    else:
        appdata = Path.home() / '.config' / 'fabric_backend'

    # Create logs directory
    app_name = config_service.get_app_name().replace(" ", "_")
    log_dir = appdata / app_name / 'logs'
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
            "color": {
                "()": "main.ColorFormatter",
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
                "formatter": "color",
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
    logger.info("Logging initialized - Level: %s, File: %s", log_level, log_file)

    return logger

# Global state for shutdown handling
class ApplicationState:
    def __init__(self) -> None:
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False
        self.logger: logging.Logger | None = None
        self.active_requests: set[str] = set()
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
    logger.info("Starting %s...", config_service.get_app_name())
    logger.info("Environment: %s", config_service.get_environment())
    logger.info("Python Version: %s", sys.version)
    logger.info("Platform: %s", sys.platform)
    logger.info("Process ID: %s", os.getpid())
    logger.info("=" * 60)

    logger.info("Configuration Summary:")
    logger.info("  - Host: %s", config_service.get_host())
    logger.info("  - Port: %s", config_service.get_port())
    logger.info("  - Debug: %s", config_service.is_debug())
    logger.info("  - Log Level: %s", config_service.get_log_level())
    logger.info("  - Shutdown Timeout: %ss", config_service.get_shutdown_timeout())

    try:
        # Initialize all services with parallel execution
        initializer = get_service_initializer()
        await initializer.initialize_all_services()

        # Initialize MCP client manager for agentic chat.
        # Construction (config load + variable resolution) is treated as
        # fatal — it would only fail on a code/config bug, and silently
        # masking it (as a previous version did) hid an IndexError that
        # broke MCP entirely in production. Per-server discovery failures
        # are still tolerated inside ``discover_tools``.
        try:
            mcp_config_path = os.path.join(os.path.dirname(__file__), "mcp_servers.json")
            mcp_manager = MCPClientManager(mcp_config_path)
        except Exception:
            logger.exception("\u2717 MCP manager construction failed (config bug)")
            raise

        try:
            await mcp_manager.discover_tools()
            set_mcp_manager(mcp_manager)
            tool_count = len(mcp_manager.tools)
            logger.info(
                "\u2713 MCP client initialized: %d tools from %d servers",
                tool_count, len(mcp_manager.config.get('servers', {})),
            )
            # Register tool-runtime policies and warn about any MCP tool
            # that was discovered but has no policy entry — the runtime
            # denies unregistered tools by default.
            from services.agenthub import tool_policies
            tool_policies.register_all()
            tool_policies.warn_about_unregistered(list(mcp_manager.tools.keys()))
        except Exception:
            logger.warning("\u26a0 MCP tool discovery failed (chat will work without tools)", exc_info=True)
            mcp_manager = None

        # Initialize AgentHub database
        try:
            agenthub_store.init_db()
            logger.info("\u2713 AgentHub database initialized")
        except Exception:
            logger.error(
                "\u2717 AgentHub DB init failed \u2014 /api/sessions and related endpoints "
                "will return 500 until the DB path is reachable",
                exc_info=True,
            )

        # Configure orchestrator engine
        if mcp_manager:
            get_orchestrator_engine().configure(mcp_manager, _get_copilot_token, _acquire_mcp_tokens)
            logger.info("\u2713 Orchestrator engine configured")

        startup_time = time.time() - startup_start
        logger.info("\u2713 Application started successfully in %.2fs", startup_time)
        logger.info("\u2713 Server: %s", config_service.get_http_endpoint())
        logger.info("\u2713 Debug Mode: %s", config_service.is_debug())
        logger.info("=" * 60)

    except Exception:
        logger.exception("Failed to start application")
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
        logger.info("Cleaning up background tasks (timeout: %.1fs)...", tasks_cleanup_timeout)
        await cleanup_background_tasks(timeout=tasks_cleanup_timeout)
        logger.info("\u2713 Background tasks cleanup completed")
    except Exception:
        logger.exception("Error during background tasks cleanup")

    # 2. Clean up services
    try:
        registry = get_service_registry()
        logger.info("Cleaning up services (timeout: %.1fs)...", service_cleanup_timeout)
        await asyncio.wait_for(registry.cleanup(), timeout=service_cleanup_timeout)
        logger.info("\u2713 Service registry cleanup completed")
    except TimeoutError:
        logger.warning("\u26a0 Service registry cleanup timed out")
    except Exception:
        logger.exception("Error during service registry cleanup")

    shutdown_duration = time.time() - shutdown_start_time
    logger.info("\u2713 Application shutdown completed in %.2fs", shutdown_duration)
    logger.info("=" * 60)

# Create FastAPI app
# ─── Security headers ─────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds baseline security response headers.

    Fabric-embedded workloads are loaded inside the Fabric portal iframe, so
    ``frame-ancestors`` is explicitly set to the Fabric hosts rather than
    ``'none'``. HSTS is only set when the request arrives over HTTPS (or when
    a trusted proxy has annotated ``X-Forwarded-Proto: https``) to avoid
    poisoning local-dev HTTP origins.
    """

    _FABRIC_FRAME_ANCESTORS = (
        "https://app.fabric.microsoft.com "
        "https://msit.fabric.microsoft.com "
        "https://dxt.fabric.microsoft.com "
        "https://df.fabric.microsoft.com"
    )

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        headers = response.headers

        # Content Security Policy: the backend serves JSON/SSE only — no
        # inline scripts should ever execute from backend responses. Frame
        # ancestors are restricted to the Fabric portal origins.
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; "
            "frame-ancestors " + self._FABRIC_FRAME_ANCESTORS + "; "
            "base-uri 'none'; form-action 'none'",
        )
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Cross-Origin-Resource-Policy", "cross-origin")

        # HSTS only when the edge is HTTPS.
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        is_https = request.url.scheme == "https" or forwarded_proto == "https"
        if is_https:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Acknowledge Chrome's Private Network Access preflight.

    When a page on a public origin (e.g. https://app.powerbi.com) fetches
    a resource from a private IP / loopback address, Chrome sends a
    preflight carrying ``Access-Control-Request-Private-Network: true``.
    Unless the server answers with ``Access-Control-Allow-Private-Network:
    true`` the request is blocked (warnings today, hard-blocks on recent
    Chrome).

    The header is only added when the request actually asks for PNA, so
    production traffic (which never sends that header) is unaffected.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network", "").lower() == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config_service = get_configuration_service()

    app = FastAPI(
        title=config_service.get_app_name(),
        description="Fabric AgentHub — backend",
        version="1.0.0",
        root_path="/workload",
        lifespan=lifespan,
        docs_url="/api/docs" if config_service.is_debug() else None,
        redoc_url="/api/redoc" if config_service.is_debug() else None,
        openapi_url="/api/openapi.json" if config_service.is_debug() else None
    )

    # Configure middleware

    # Security headers — applied to every response, always on.
    app.add_middleware(SecurityHeadersMiddleware)

    # Security middleware (only in production)
    if config_service.is_production():
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=config_service.get_allowed_hosts()
        )

    # Compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # CORS — explicit methods/headers (never wildcards with credentials).
    # In production we use an explicit origin allowlist from config. In
    # development we additionally enable an origin regex so the Fabric /
    # PowerBI portal (and their many subdomains) plus any localhost port
    # are accepted without fiddling with appsettings on every dev machine.
    cors_kwargs = dict(
        allow_origins=config_service.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Fabric-Token",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", "X-Process-Time"],
        max_age=600,
    )
    if config_service.is_debug():
        # Dev only: accept any localhost port, any *.fabric.microsoft.com,
        # *.powerbi.com and *.analysis.windows.net subdomain. Never enabled
        # in production (``is_debug()`` is false there).
        cors_kwargs["allow_origin_regex"] = (
            r"^(https?://localhost(:\d+)?"
            r"|https?://127\.0\.0\.1(:\d+)?"
            r"|https://([a-z0-9-]+\.)*fabric\.microsoft\.com"
            r"|https://([a-z0-9-]+\.)*powerbi\.com"
            r"|https://([a-z0-9-]+\.)*analysis\.windows\.net)$"
        )
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Private Network Access — must be registered AFTER CORSMiddleware so
    # it wraps the outside of the CORS middleware's preflight response and
    # adds ``Access-Control-Allow-Private-Network: true`` when Chrome asks.
    app.add_middleware(PrivateNetworkAccessMiddleware)

    # Register exception handlers
    register_exception_handlers(app)

    # Include routers with proper prefixes
    app.include_router(EndpointResolutionApiRouter)
    app.include_router(ItemLifecycleApiRouter)
    app.include_router(JobsApiRouter)
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
        "timestamp": datetime.now(UTC).isoformat(),
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
                    "timestamp": datetime.now(UTC).isoformat()
                }
            )

        return {
            "status": "ready",
            "timestamp": datetime.now(UTC).isoformat(),
            "services": registry.get_all_services()
        }
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not ready",
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat()
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
                "%s %s \u2192 %s (%.3fs) [ID: %s]",
                request.method, request.url.path, response.status_code, process_time, request_id[:8],
            )

        return response

    except Exception as e:
        process_time = time.time() - start_time
        if app_state.logger:
            app_state.logger.error(
                "%s %s \u2192 ERROR (%.3fs) [ID: %s]: %s",
                request.method, request.url.path, process_time, request_id[:8], e,
                exc_info=True,
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
