import pytest
import os
from httpxthrottlecache import HttpxThrottleCache, EDGAR_CACHE_RULES
import logging 
import httpx
import httpxthrottlecache

logger = logging.getLogger(__name__ )
logging.basicConfig(
    format='%(asctime)s %(name)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

@pytest.fixture(params=["FileCache"], ids=["filecache"])
def manager_cache(tmp_path_factory, request):
    user_agent = os.environ.get("EDGAR_IDENTITY", None)
    cache_dir = tmp_path_factory.mktemp("cache")

    logger.debug("Test cache_dir=%s", cache_dir)
    return HttpxThrottleCache(user_agent=user_agent, cache_dir=cache_dir, cache_mode=request.param, cache_rules=EDGAR_CACHE_RULES)

@pytest.fixture
def manager_nocache():
    user_agent = os.environ.get("EDGAR_IDENTITY", None)
    logger.debug("Non-caching manager")
    mgr = HttpxThrottleCache(user_agent=user_agent, cache_mode="Disabled", cache_dir=None)
    return mgr


def mock_client(client):

    class _MockAsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self): yield b"ok"
        async def aclose(self): pass

    async def _handler(req): 
        return httpx.Response(200, headers={"date":"Mon, 01 Jan 2024 00:00:00 GMT"}, request=req, stream=_MockAsyncStream())

    next_transport = httpx.MockTransport(_handler)

    if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
        client._transport.transport = next_transport
    elif isinstance(client._transport, httpxthrottlecache.ratelimiter.RateLimitingTransport):
        client._transport.handle_request = next_transport.handle_request
    elif isinstance(client._transport, httpxthrottlecache.ratelimiter.AsyncRateLimitingTransport):
        client._transport.handle_async_request = next_transport.handle_async_request
    else:
        raise AssertionError(f"Unexpected transport type: {type(client._transport)}")