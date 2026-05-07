"""Local OpenTelemetry bootstrap for AgentHub.

The first observability target is intentionally local: emit sampled spans to a
JSONL file under the bind-mounted data directory, and optionally to stdout for
short debugging bursts. Exporters for Azure Monitor or an OTLP collector can be
added later without changing application instrumentation points.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger(__name__)

_PROVIDER_CONFIGURED = False
_INSTRUMENTED_APP_IDS: set[int] = set()
_TRACE_FILE_HANDLE: TextIO | None = None


def configure_local_opentelemetry(app: Any, *, service_name: str, service_version: str | None = None) -> bool:
    """Configure local OpenTelemetry tracing for the FastAPI app.

    Returns ``True`` when instrumentation is enabled. Missing optional packages
    do not break startup; the app keeps running with regular logging.
    """
    if not _enabled():
        logger.info("OpenTelemetry local tracing disabled by AGENTHUB_OTEL_ENABLED")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
        from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased, TraceIdRatioBased
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry requested but packages are not installed: %s",
            exc,
        )
        return False

    global _PROVIDER_CONFIGURED, _TRACE_FILE_HANDLE
    if not _PROVIDER_CONFIGURED:
        sample_rate = _sample_rate()
        sampler = ALWAYS_ON if sample_rate >= 1 else ALWAYS_OFF if sample_rate <= 0 else ParentBased(TraceIdRatioBased(sample_rate))
        resource = Resource.create(
            {
                SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name),
                SERVICE_VERSION: service_version or os.environ.get("OTEL_SERVICE_VERSION", "dev"),
                DEPLOYMENT_ENVIRONMENT: os.environ.get("PYTHON_ENVIRONMENT", "Development"),
                "service.namespace": "developer-hub",
            }
        )
        provider = TracerProvider(resource=resource, sampler=sampler)

        if _truthy(os.environ.get("AGENTHUB_OTEL_CONSOLE_EXPORTER", "0")):
            provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter(formatter=_compact_span_json))
            )

        trace_file = os.environ.get("AGENTHUB_OTEL_TRACE_FILE", "").strip()
        if trace_file:
            trace_path = Path(trace_file)
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            _TRACE_FILE_HANDLE = trace_path.open("a", encoding="utf-8")
            provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter(out=_TRACE_FILE_HANDLE, formatter=_compact_span_json))
            )

        try:
            trace.set_tracer_provider(provider)
        except Exception:
            logger.warning("OpenTelemetry tracer provider was already configured; using existing provider", exc_info=True)

        _instrument_once(HTTPXClientInstrumentor(), "httpx")
        _instrument_once(RequestsInstrumentor(), "requests")
        _instrument_once(AioHttpClientInstrumentor(), "aiohttp-client")
        _instrument_once(SQLite3Instrumentor(), "sqlite3")

        _PROVIDER_CONFIGURED = True
        logger.info(
            "OpenTelemetry local tracing enabled sample_rate=%.3f console=%s trace_file=%s",
            sample_rate,
            _truthy(os.environ.get("AGENTHUB_OTEL_CONSOLE_EXPORTER", "0")),
            trace_file or "-",
        )

    app_id = id(app)
    if app_id not in _INSTRUMENTED_APP_IDS:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=os.environ.get("AGENTHUB_OTEL_EXCLUDED_URLS", ""),
            server_request_hook=_server_request_hook,
            client_response_hook=_client_response_hook,
        )
        _INSTRUMENTED_APP_IDS.add(app_id)
        logger.info("OpenTelemetry FastAPI instrumentation attached to app id=%s", app_id)

    return True


def shutdown_local_opentelemetry() -> None:
    """Flush and close local OpenTelemetry resources."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        logger.warning("OpenTelemetry shutdown failed", exc_info=True)
    finally:
        global _TRACE_FILE_HANDLE
        if _TRACE_FILE_HANDLE is not None:
            try:
                _TRACE_FILE_HANDLE.close()
            finally:
                _TRACE_FILE_HANDLE = None


def _instrument_once(instrumentor: Any, name: str) -> None:
    try:
        instrumentor.instrument()
        logger.info("OpenTelemetry instrumented %s", name)
    except Exception:
        logger.warning("OpenTelemetry could not instrument %s", name, exc_info=True)


def _server_request_hook(span: Any, scope: dict[str, Any]) -> None:
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    headers = _headers_from_scope(scope)
    request_id = headers.get("x-request-id") or "-"
    span.set_attribute("agenthub.request_id", request_id)
    span.set_attribute("agenthub.http.path", scope.get("path", ""))
    span.set_attribute("agenthub.http.root_path", scope.get("root_path", ""))
    span.set_attribute("agenthub.auth.authorization_present", "authorization" in headers)
    span.set_attribute("agenthub.auth.fabric_token_present", "x-fabric-token" in headers)


def _client_response_hook(span: Any, message: dict[str, Any]) -> None:
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    if message.get("type") == "http.response.start":
        span.set_attribute("agenthub.http.response_status", int(message.get("status") or 0))


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        try:
            headers[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")
        except Exception:
            continue
    return headers


def _compact_span_json(span: Any) -> str:
    try:
        payload = json.loads(span.to_json())
    except Exception:
        payload = {"name": getattr(span, "name", "unknown")}
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True) + "\n"


def _enabled() -> bool:
    return _truthy(os.environ.get("AGENTHUB_OTEL_ENABLED", "1"))


def _sample_rate() -> float:
    raw = os.environ.get("AGENTHUB_OTEL_SAMPLE_RATE", "1.0")
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 1.0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}