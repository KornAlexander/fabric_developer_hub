import logging
import httpx
import asyncio
from typing import Dict, Any, Optional, ClassVar

class HttpClientService:
    """
    Singleton HTTP client service with connection pooling and retry logic.
    Managed by ServiceRegistry for proper lifecycle management.
    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._closed = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0
            ),
            follow_redirects=True,
            event_hooks={
                "request": [self._log_request],
                "response": [self._log_response]
            }
        )

    async def _log_request(self, request):
        self.logger.debug(f"Request: {request.method} {request.url}")
    
    async def _log_response(self, response):
        request = response.request
        try:
            elapsed_time = 0
            if hasattr(response, '_elapsed') and response._elapsed:
                elapsed_time = response._elapsed.total_seconds()
            
            self.logger.debug(
                f"Response: {request.method} {request.url} - "
                f"Status: {response.status_code} - Time: {elapsed_time:.2f}s"
            )
        except Exception:
            # If we can't get timing, just log without it
            self.logger.debug(
                f"Response: {request.method} {request.url} - "
                f"Status: {response.status_code}"
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
            except Exception as e:
                self.logger.error(f"Error closing HTTP client: {e}")
                self._closed = True  # Mark as closed anyway

    async def dispose_async(self):
        """Dispose method for ServiceRegistry cleanup."""
        await self.close()
    
    def _get_headers(self, token: str) -> Dict[str, str]:
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
        for attempt in range(max_retries):
            try:
                response = await getattr(self._client, method)(
                    url, headers=headers, **kwargs
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning(
                        f"Request failed with {e.response.status_code}, "
                        f"retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise
            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.warning(
                        f"Request error: {e}, retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise

    async def get(self, url: str, token: str) -> httpx.Response:
        """Performs a GET request to the specified URL."""
        return await self._make_request('get', url, token)
    
    async def put(self, url: str, content: Any, token: str) -> httpx.Response:
        """Performs a PUT request to the specified URL."""
        kwargs = {}
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
        kwargs = {}
        if isinstance(content, (str, bytes)):
            if isinstance(content, str):
                content = content.encode('utf-8')
            kwargs['content'] = content
        else:
            kwargs['json'] = content
            kwargs['headers'] = {"Content-Type": "application/json"}
        
        return await self._make_request('post', url, token, **kwargs)
    
    async def patch(self, url: str, content: Optional[Any], token: str, 
                   content_type: Optional[str] = None) -> httpx.Response:
        """Performs a PATCH request to the specified URL."""
        kwargs = {}
        headers = {}
        
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
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    try:
        return registry.get(HttpClientService)
    except KeyError:
        logger = logging.getLogger(__name__)
        logger.error("HttpClientService not found in registry. Was the service initialized?")
        raise RuntimeError(
            "HttpClientService not initialized. Please ensure ServiceInitializer.initialize_all_services() "
            "has been called during application startup."
        )