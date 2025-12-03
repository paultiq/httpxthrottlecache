import asyncio
import importlib.util
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Generator, Literal, Mapping, Optional, Sequence, Union

import httpx
from httpx._types import ProxyTypes
from pyrate_limiter import Duration, Limiter

from .filecache.transport import CachingTransport
from .ratelimiter import AsyncRateLimitingTransport, RateLimitingTransport, create_rate_limiter

logger = logging.getLogger(__name__)

HTTP2 = importlib.util.find_spec("h2") is not None


@dataclass
class HttpxThrottleCache:
    """
    Implements a rate limited, optional-cached HTTPX wrapper that returns client() (httpx.Client) or async_http_client() (httpx.AsyncClient).

    Rate Limiting is across all connections, whether via client & async_htp_client, using pyrate_limiter. For multiprocessing, use pyrate_limiters
    MultiprocessBucket or SqliteBucket w/ a file lock.

    This function is used for all synchronous requests.
    """

    httpx_params: dict[str, Any] = field(
        default_factory=lambda: {"default_encoding": "utf-8", "http2": HTTP2, "verify": True}
    )

    cache_rules: dict[str, dict[str, Union[bool, int]]] = field(default_factory=lambda: {})
    rate_limiter_enabled: bool = True
    cache_mode: Literal[False, "Disabled", "FileCache"] = "FileCache"
    request_per_sec_limit: int = 10
    max_delay: Duration = field(default_factory=lambda: Duration.DAY)
    _client: Optional[httpx.Client] = None

    rate_limiter: Optional[Limiter] = None
    user_agent: Optional[str] = None
    user_agent_factory: Optional[Callable[[], str]] = None

    cache_dir: Optional[Union[Path, str]] = None

    lock = threading.Lock()

    proxy: Optional[ProxyTypes] = None

    def __post_init__(self):
        if self.cache_mode == "Hishel-File":
            logger.debug("Hishel-File is deprecated and will be removed, due to breaking API changes")
            self.cache_mode = "FileCache"
        self.cache_dir = Path(self.cache_dir) if isinstance(self.cache_dir, str) else self.cache_dir
        # self.lock = threading.Lock()

        if self.rate_limiter_enabled and self.rate_limiter is None:
            self.rate_limiter = create_rate_limiter(
                requests_per_second=self.request_per_sec_limit, max_delay=self.max_delay
            )

        if (self.cache_mode != "Disabled" or self.cache_mode is False) and not self.cache_rules:
            logger.info("Cache is enabled, but no cache_rules provided. Will use default caching.")

        if self.cache_mode == "Disabled" or self.cache_mode is False:
            pass
        elif self.cache_mode == "FileCache":
            if self.cache_dir is None:
                raise ValueError(f"cache_dir must be provided if using a file based cache: {self.cache_mode}")
            else:
                if not self.cache_dir.exists():
                    self.cache_dir.mkdir()
        else:
            raise ValueError(f"Unsupported cache_mode: {self.cache_mode}")

        logger.debug(
            "Initialized cache with cache_mode=%s, cache_dir=%s, rate_limiter_enabled=%s",
            self.cache_mode,
            self.cache_dir,
            self.rate_limiter_enabled,
        )

        if os.environ.get("HTTPS_PROXY") is not None:
            self.proxy = os.environ.get("HTTPS_PROXY")

    def _populate_user_agent(self, params: dict[str, Any]):
        if self.user_agent_factory is not None:
            user_agent = self.user_agent_factory()
        else:
            user_agent = self.user_agent

        if user_agent is not None:
            if "headers" not in params:
                params["headers"] = {}

            params["headers"]["User-Agent"] = user_agent
        return params

    def populate_user_agent(self, params: dict[str, Any]):
        """Provided so clients can inspect the params that would be passed to HTTPX"""
        return self._populate_user_agent(params)

    def get_batch(
        self,
        *,
        urls: Sequence[str] | Mapping[str, Path],
        _client_mocker: Optional[Callable[[httpx.AsyncClient], httpx.AsyncClient]] = None,
    ):
        """
        Fetch a batch of URLs concurrently and either return their content in-memory
        or stream them directly to files.

        Uses background thread with an asyncio event loop.

        Args:
            urls (Sequence[str] | Mapping[str, Path]):

        Returns:
            list:
                - If given mappings, then returns a list of Paths. Else, a list of the Content

        Raises:
            RuntimeError: If any URL responds with a status code other than 200 or 304.
        """

        import aiofiles

        async def _run() -> Sequence[Path | bytes]:
            async with self.async_http_client() as client:
                if _client_mocker:
                    # For testing
                    _client_mocker(client)

                async def task(url: str, path: Optional[Path]) -> Path | bytes:
                    async with client.stream("GET", url) as r:
                        if r.status_code in (200, 304):
                            if path:
                                path.parent.mkdir(parents=True, exist_ok=True)
                                async with aiofiles.open(path, "wb") as f:
                                    async for chunk in r.aiter_bytes():
                                        await f.write(chunk)
                                return path
                            else:
                                return await r.aread()
                        else:
                            raise RuntimeError(f"URL status code is not 200 or 304: {url=}")

                if isinstance(urls, Mapping):
                    return await asyncio.gather(*(task(u, p) for u, p in urls.items()))
                else:
                    return await asyncio.gather(*(task(u, None) for u in urls))

        with ThreadPoolExecutor(1) as pool:
            return pool.submit(lambda: asyncio.run(_run())).result()

    def _get_httpx_transport_params(self, params: dict[str, Any]):
        http2 = params.get("http2", False)
        proxy = self.proxy

        return {"http2": http2, "proxy": proxy}

    @contextmanager
    def http_client(self, bypass_cache: bool = False, **kwargs: dict[str, Any]) -> Generator[httpx.Client, None, None]:
        """Provides and reuses a client. Does not close"""
        if self._client is None:
            with self.lock:
                # Locking: not super critical, since worst case might be extra httpx clients created,
                # but future proofing against TOCTOU races in free-threading world
                if self._client is None:
                    logger.debug("Creating new HTTPX Client")
                    params = self.httpx_params.copy()

                    self._populate_user_agent(params)

                    params.update(**kwargs)
                    params["transport"] = self._get_transport(
                        bypass_cache=bypass_cache, httpx_transport_params=self._get_httpx_transport_params(params)
                    )
                    self._client = httpx.Client(**params)

        yield self._client

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def update_rate_limiter(self, requests_per_second: int, max_delay: Duration = Duration.DAY):
        self.rate_limiter = create_rate_limiter(requests_per_second=requests_per_second, max_delay=Duration.DAY)

        self.close()

    def _client_factory_async(self, bypass_cache: bool, **kwargs: dict[str, Any]) -> httpx.AsyncClient:
        params = self.httpx_params.copy()
        params.update(**kwargs)
        self._populate_user_agent(params)
        params["transport"] = self._get_async_transport(
            bypass_cache=bypass_cache, httpx_transport_params=self._get_httpx_transport_params(params)
        )

        return httpx.AsyncClient(**params)

    @asynccontextmanager
    async def async_http_client(
        self, client: Optional[httpx.AsyncClient] = None, bypass_cache: bool = False, **kwargs: dict[str, Any]
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """
        Async callers should create a single client for a group of tasks, rather than creating a single client per task.

        If a null client is passed, then this is a no-op and the client isn't closed. This (passing a client) occurs when a higher level async task creates the client to be used by child calls.
        """

        if client is not None:
            yield client  # type: ignore # Caller is responsible for closing
            return

        async with self._client_factory_async(bypass_cache=bypass_cache, **kwargs) as client:
            yield client

    def _get_transport(self, bypass_cache: bool, httpx_transport_params: dict[str, Any]) -> httpx.BaseTransport:
        """
        Constructs the Transport Chain:

        Caching Transport (if enabled) => Rate Limiting Transport (if enabled) => httpx.HTTPTransport
        """
        if self.rate_limiter_enabled:
            assert self.rate_limiter is not None
            next_transport = RateLimitingTransport(self.rate_limiter, **httpx_transport_params)
        else:
            next_transport = httpx.HTTPTransport(**httpx_transport_params)

        if bypass_cache or self.cache_mode == "Disabled" or self.cache_mode is False:
            logger.info("Cache is DISABLED, rate limiting only")
            return next_transport
        elif self.cache_mode == "FileCache":
            assert self.cache_dir is not None
            return CachingTransport(cache_dir=self.cache_dir, transport=next_transport, cache_rules=self.cache_rules)
        else:
            raise ValueError(f"Unsupported cache_mode: {self.cache_mode}")

    def _get_async_transport(
        self, bypass_cache: bool, httpx_transport_params: dict[str, Any]
    ) -> httpx.AsyncBaseTransport:
        """
        Constructs the Transport Chain:

        Caching Transport (if enabled) => Rate Limiting Transport (if enabled) => httpx.HTTPTransport
        """

        if self.rate_limiter_enabled:
            assert self.rate_limiter is not None
            next_transport = AsyncRateLimitingTransport(self.rate_limiter, **httpx_transport_params)
        else:
            next_transport = httpx.AsyncHTTPTransport(**httpx_transport_params)

        if bypass_cache or self.cache_mode == "Disabled" or self.cache_mode is False:
            logger.info("Cache is DISABLED, rate limiting only")
            return next_transport
        elif self.cache_mode == "FileCache":
            assert self.cache_dir is not None
            return CachingTransport(cache_dir=self.cache_dir, transport=next_transport, cache_rules=self.cache_rules)  # pyright: ignore[reportArgumentType]
        else:
            raise ValueError(f"Unsupported cache_mode: {self.cache_mode}")

    def __enter__(self):
        return self

    def __exit__(self, type: Any, value: Any, traceback: Any):
        self.close()
