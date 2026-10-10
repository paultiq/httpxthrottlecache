"""httpx transports that send requests through primp's browser-impersonating client."""

import logging
from typing import Any, AsyncIterator, Iterator, Optional

import primp
from pyrate_limiter import Limiter

from ._compat import httpx

logger = logging.getLogger(__name__)

LIMITER_ITEM = "httpxthrottlecache.ratelimiter"

_PRIMP_OWNED_HEADERS = frozenset(
    {
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "accept-encoding",
        "host",
        "connection",
        "content-length",
    }
)
_STALE_RESPONSE_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


def _client_params(primp_params: dict[str, Any], proxy: Any, verify: Any) -> dict[str, Any]:
    # httpx owns cookies and redirects, so each redirect hop passes the cache and the limiter.
    params: dict[str, Any] = {"cookie_store": False, **primp_params, "follow_redirects": False}
    if isinstance(proxy, str):
        params.setdefault("proxy", proxy)
    if isinstance(verify, bool):
        params.setdefault("verify", verify)
    return params


def _request_kwargs(request: httpx.Request) -> dict[str, Any]:
    return {
        "headers": {k: v for k, v in request.headers.items() if k.lower() not in _PRIMP_OWNED_HEADERS},
        "timeout": request.extensions.get("timeout", {}).get("read"),
        "follow_redirects": False,
        "stream": True,
    }


def _map_error(e: Exception, request: httpx.Request) -> httpx.RequestError:
    if isinstance(e, primp.TimeoutError):
        return httpx.TimeoutException(str(e), request=request)
    if isinstance(e, (primp.ConnectError, primp.DNSError)):
        return httpx.ConnectError(str(e), request=request)
    if isinstance(e, primp.DecodeError):
        return httpx.DecodingError(str(e), request=request)
    if isinstance(e, primp.BodyError):
        return httpx.ReadError(str(e), request=request)
    return httpx.TransportError(str(e), request=request)


def _to_httpx_response(r: Any, request: httpx.Request, stream: Any) -> httpx.Response:
    headers = [(k, v) for k, v in r.headers.items() if k.lower() not in _STALE_RESPONSE_HEADERS]
    return httpx.Response(r.status_code, headers=headers, stream=stream, request=request)


class _PrimpStream(httpx.SyncByteStream):
    def __init__(self, resp: primp.Response, request: httpx.Request):
        self.resp, self.request = resp, request

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self.resp.iter_bytes()
        except primp.PrimpError as e:
            logger.warning("primp body read failed for %s: %s", self.request, e)
            raise _map_error(e, self.request) from e

    def close(self) -> None:
        self.resp.close()


class _AsyncPrimpStream(httpx.AsyncByteStream):
    def __init__(self, resp: primp.AsyncResponse, request: httpx.Request):
        self.resp, self.request = resp, request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.resp.aiter_bytes():
                yield chunk
        except primp.PrimpError as e:
            logger.warning("primp body read failed for %s: %s", self.request, e)
            raise _map_error(e, self.request) from e

    async def aclose(self) -> None:
        await self.resp.aclose()


class PrimpTransport(httpx.BaseTransport):
    def __init__(self, limiter: Optional[Limiter], primp_params: dict[str, Any], proxy: Any = None, verify: Any = True):
        self.limiter = limiter
        self.client = primp.Client(**_client_params(primp_params, proxy, verify))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.limiter:
            self.limiter.try_acquire(LIMITER_ITEM)
            logger.debug("Acquired lock")

        logger.info("Making primp HTTP Request %s", request)
        try:
            r = self.client.request(
                request.method, str(request.url), content=request.read() or None, **_request_kwargs(request)
            )
        except primp.PrimpError as e:
            logger.warning("primp request failed for %s: %s", request, e)
            raise _map_error(e, request) from e
        return _to_httpx_response(r, request, _PrimpStream(r, request))


class AsyncPrimpTransport(httpx.AsyncBaseTransport):
    def __init__(self, limiter: Optional[Limiter], primp_params: dict[str, Any], proxy: Any = None, verify: Any = True):
        self.limiter = limiter
        self.client = primp.AsyncClient(**_client_params(primp_params, proxy, verify))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.limiter:
            await self.limiter.try_acquire_async(LIMITER_ITEM)
            logger.debug("Acquired lock")

        logger.info("Making primp HTTP Request %s", request)
        try:
            r = await self.client.request(
                request.method, str(request.url), content=await request.aread() or None, **_request_kwargs(request)
            )
        except primp.PrimpError as e:
            logger.warning("primp request failed for %s: %s", request, e)
            raise _map_error(e, request) from e
        return _to_httpx_response(r, request, _AsyncPrimpStream(r, request))
