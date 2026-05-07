import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.service_registry import get_service_registry
from services.correlation import get_request_id
from services.logging_categories import log_extra
from services.observability import safe_url


class HttpClientService:
    """
    Singleton HTTP client service with connection pooling and retry logic.
    Managed by ServiceRegistry for proper lifecycle management.
    """
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._closed = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0
            ),
            follow_redirects=False,
            event_hooks={
                "request": [self._inject_request_id, self._log_request],
                "response": [self._log_response]
            }
        )

    async def _inject_request_id(self, request: httpx.Request) -> None:
        """Propagate the current inbound request's ID to every outbound call.

        Lets log lines on downstream services (Fabric REST, Copilot, MCP) be
        correlated back to the originating user action. Only set when the
        caller hasn't already supplied an ``X-Request-ID`` header.
        """
        if "X-Request-ID" not in request.headers:
            rid = get_request_id()
            if rid and rid != "-":
                request.headers["X-Request-ID"] = rid

    async def _log_request(self, request: httpx.Request) -> None:
        request.extensions["agenthub_started_at"] = time.monotonic()
        self.logger.info(
            "[HTTP-OUT] start %s %s content_length=%s",
            request.method,
            safe_url(request.url),
            request.headers.get("content-length", "0"),
            extra=log_extra("diagnostic"),
        )

    async def _log_response(self, response: httpx.Response) -> None:
        request = response.request
        try:
            elapsed_time = None
            started = request.extensions.get("agenthub_started_at")
            if isinstance(started, (int, float)):
                elapsed_time = time.monotonic() - started

            self.logger.info(
                "[HTTP-OUT] end %s %s status=%s elapsed=%.3fs response_length=%s",
                request.method,
                safe_url(request.url),
                response.status_code,
                elapsed_time if elapsed_time is not None else -1,
                response.headers.get("content-length", "unknown"),
                extra=log_extra("diagnostic" if response.status_code < 500 else "high_level"),
            )
        except Exception:
            # If we can't get timing, just log without it
            self.logger.info(
                "[HTTP-OUT] end %s %s status=%s",
                request.method,
                safe_url(request.url),
                response.status_code,
                extra=log_extra("diagnostic"),
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close the HTTP client."""
        if not self._closed and hasattr(self, '_client'):
            try:
                # Check if we're in an async context
                try:
                    asyncio.get_running_loop()
                    await self._client.aclose()
                    self._closed = True
                    self.logger.info("HTTP client closed successfully")
                except RuntimeError:
                    # Not in async context, try sync close if available
                    if hasattr(self._client, 'close'):
                        self._client.close()
                    self._closed = True
                    self.logger.warning("HTTP client closed outside async context")
            except Exception:
                self.logger.exception("Error closing HTTP client")
                self._closed = True  # Mark as closed anyway

    async def dispose_async(self) -> None:
        """Dispose method for ServiceRegistry cleanup."""
        await self.close()

    @property
    def raw_client(self) -> httpx.AsyncClient:
        """Underlying pooled ``httpx.AsyncClient`` for callers that cannot use the
        token-wrapping helpers.

        Callers that need to hit non-Fabric endpoints with non-Bearer auth
        (e.g. GitHub Device Flow, the Copilot ``api.githubcopilot.com`` API
        which expects its own token format, health probes) should use this
        instead of spinning up a new ``httpx.AsyncClient`` per request. The
        client is process-wide and owned by ``ServiceRegistry`` — do NOT
        close it.
        """
        return self._client

    def _get_headers(self, token: str) -> dict[str, str]:
        """Create headers with proper authorization."""
        headers = {}
        if token.startswith("SubjectAndAppToken"):
            headers["Authorization"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        headers["User-Agent"] = "Microsoft-Fabric-Workload/1.0"
        return headers

    async def _make_request(self, method: str, url: str, token: str, **kwargs) -> httpx.Response:
        """Common request handling with retry logic."""
        headers = self._get_headers(token)
        headers.update(kwargs.pop('headers', {}))

        max_retries = 3
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            attempt_start = time.monotonic()
            try:
                self.logger.info(
                    "[HTTP-CLIENT] attempt %d/%d %s %s",
                    attempt + 1,
                    max_retries,
                    method.upper(),
                    safe_url(url),
                    extra=log_extra("diagnostic"),
                )
                response = await getattr(self._client, method)(
                    url, headers=headers, **kwargs
                )
                response.raise_for_status()
                self.logger.info(
                    "[HTTP-CLIENT] success %s %s status=%s attempt=%d elapsed=%.3fs",
                    method.upper(),
                    safe_url(url),
                    response.status_code,
                    attempt + 1,
                    time.monotonic() - attempt_start,
                    extra=log_extra("diagnostic"),
                )
                return response
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning(
                        "Request failed with %s, retrying in %ss (attempt %d/%d)",
                        e.response.status_code, wait_time, attempt + 1, max_retries,
                        extra=log_extra("high_level"),
                    )
                    await asyncio.sleep(wait_time)
                    continue
                self.logger.warning(
                    "[HTTP-CLIENT] failed %s %s status=%s attempt=%d elapsed=%.3fs body=%.300s",
                    method.upper(),
                    safe_url(url),
                    e.response.status_code,
                    attempt + 1,
                    time.monotonic() - attempt_start,
                    e.response.text,
                    extra=log_extra("high_level"),
                )
                raise
            except httpx.RequestError as e:
                last_exc = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning(
                        "Request error: %s, retrying in %ss (attempt %d/%d)",
                        e, wait_time, attempt + 1, max_retries,
                        extra=log_extra("high_level"),
                    )
                    await asyncio.sleep(wait_time)
                    continue
                self.logger.warning(
                    "[HTTP-CLIENT] request error %s %s attempt=%d elapsed=%.3fs error=%s",
                    method.upper(),
                    safe_url(url),
                    attempt + 1,
                    time.monotonic() - attempt_start,
                    e,
                    extra=log_extra("high_level"),
                )
                raise
        # Unreachable — every code path in the loop either returns or raises —
        # but mypy needs an explicit terminator to type ``_make_request`` as
        # "always returns ``httpx.Response``".
        assert last_exc is not None  # pragma: no cover
        raise last_exc  # pragma: no cover

    async def get(self, url: str, token: str) -> httpx.Response:
        """Performs a GET request to the specified URL."""
        return await self._make_request('get', url, token)

    async def put(self, url: str, content: Any, token: str) -> httpx.Response:
        """Performs a PUT request to the specified URL."""
        kwargs: dict[str, Any] = {}
        if content == "":
            kwargs['content'] = b""
        elif content is None:
            pass  # No content
        elif isinstance(content, (str, bytes)):
            if isinstance(content, str):
                content = content.encode("utf-8")
            kwargs['content'] = content
        else:
            # JSON content for API calls
            kwargs['json'] = content
            kwargs['headers'] = {"Content-Type": "application/json"}

        return await self._make_request('put', url, token, **kwargs)

    async def post(self, url: str, content: Any, token: str) -> httpx.Response:
        """Performs a POST request to the specified URL."""
        kwargs: dict[str, Any] = {}
        if isinstance(content, (str, bytes)):
            if isinstance(content, str):
                content = content.encode('utf-8')
            kwargs['content'] = content
        else:
            kwargs['json'] = content
            kwargs['headers'] = {"Content-Type": "application/json"}

        return await self._make_request('post', url, token, **kwargs)

    async def patch(self, url: str, content: Any | None, token: str,
                   content_type: str | None = None) -> httpx.Response:
        """Performs a PATCH request to the specified URL."""
        kwargs: dict[str, Any] = {}
        headers: dict[str, str] = {}

        if content is None:
            pass  # No content
        elif isinstance(content, bytes):
            kwargs['content'] = content
            if content_type:
                headers["Content-Type"] = content_type
        elif isinstance(content, str):
            kwargs['content'] = content.encode('utf-8')
        else:
            kwargs['json'] = content
            headers["Content-Type"] = "application/json"

        if headers:
            kwargs['headers'] = headers

        return await self._make_request('patch', url, token, **kwargs)

    async def delete(self, url: str, token: str) -> httpx.Response:
        """Performs a DELETE request to the specified URL."""
        return await self._make_request('delete', url, token)

    async def head(self, url: str, token: str) -> httpx.Response:
        """Performs a HEAD request to the specified URL."""
        return await self._make_request('head', url, token)

def get_http_client_service() -> HttpClientService:
    """
    Get the singleton HttpClientService instance from ServiceRegistry.
    This ensures proper lifecycle management and dependency injection.
    """
    registry = get_service_registry()
    try:
        return registry.get(HttpClientService)
    except KeyError:
        logger = logging.getLogger(__name__)
        logger.error("HttpClientService not found in registry. Was the service initialized?")
        raise RuntimeError(
            "HttpClientService not initialized. Please ensure ServiceInitializer.initialize_all_services() "
            "has been called during application startup."
        ) from None
