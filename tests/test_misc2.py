from httpxthrottlecache import HttpxThrottleCache
import httpx, pytest
from httpx import Response
from httpxthrottlecache import HttpxThrottleCache
import httpxthrottlecache
import os
import logging
logger=logging.getLogger(__name__)
import email.utils
import time 
import asyncio

def test_user_agent_factor(tmp_path):
    htc = HttpxThrottleCache(cache_mode="FileCache", cache_dir=tmp_path, user_agent_factory=lambda: "foo", rate_limiter_enabled=False)

    params = {}
    htc._populate_user_agent(params)
    assert params["headers"]["User-Agent"] == "foo"

def test_no_ratelimit(manager_cache):
    htc = HttpxThrottleCache(cache_mode=manager_cache.cache_mode, cache_dir=manager_cache.cache_dir, user_agent_factory=lambda: "foo", rate_limiter_enabled=False)
    url = "https://example.com/file.bin"
    dt = email.utils.formatdate(usegmt=True)
    chunks = [b"x" * (1 * 1)] * 4
    total = sum(map(len, chunks))
    class _Chunks(httpx.ByteStream):
        def __init__(self, cs): 
            self._cs = cs
            super().__init__(self)

        def __iter__(self):
            for c in self._cs:
                yield c
        def close(self): 
            pass

    calls = 0
    def handler(req):
        nonlocal calls
        calls += 1
        return Response(
            200,
            headers={
                "Content-Length": str(total),
                "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                "Date": dt
            },
            stream=_Chunks(chunks),
            request=req
        )

    with htc.http_client() as client:
        next_transport = httpx.MockTransport(handler)

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")
        start = time.perf_counter()
        results = [client.get(url) for r in range(30)]
        end = time.perf_counter()

        assert (end-start) < 1
        

@pytest.mark.asyncio
async def test_no_ratelimit_async(manager_cache):
    htc = HttpxThrottleCache(cache_mode=manager_cache.cache_mode, cache_dir=manager_cache.cache_dir, user_agent_factory=lambda: "foo", rate_limiter_enabled=False)
    url = "https://example.com/file.bin"
    dt = email.utils.formatdate(usegmt=True)
    chunks = [b"x" * (1 * 1)] * 4
    total = sum(map(len, chunks))
    class _Chunks(httpx.ByteStream):
        def __init__(self, cs): 
            self._cs = cs
            super().__init__(self)

        def __iter__(self):
            for c in self._cs:
                yield c
        def close(self): 
            pass

    calls = 0
    def handler(req):
        nonlocal calls
        calls += 1
        return Response(
            200,
            headers={
                "Content-Length": str(total),
                "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                "Date": dt
            },
            stream=_Chunks(chunks),
            request=req
        )

    async with htc.async_http_client() as client:
        next_transport = httpx.MockTransport(handler)

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")
        start = time.perf_counter()
        results = asyncio.gather(*[client.get(url) for r in range(30)])
        end = time.perf_counter()

        assert (end-start) < 3
        
def test_post_not_cached(manager_cache, monkeypatch):
    calls = 0
    url = "https://example.com/post"

    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"ok", request=req)

    next_transport = httpx.MockTransport(handler)
    with manager_cache.http_client() as client:

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")
        
        with client.stream("POST", url, content=b"x") as r1:
            assert r1.headers.get("x-cache") != "HIT" and r1.extensions.get("from_cache") is not True

        with client.stream("POST", url, content=b"x") as r2:
            assert r2.headers.get("x-cache") != "HIT" and r2.extensions.get("from_cache") is not True

    assert calls == 2

@pytest.mark.asyncio
async def test_post_not_cached_async(manager_cache, monkeypatch):
    calls = 0
    url = "https://example.com/post"

    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"ok", request=req)

    next_transport = httpx.MockTransport(handler)
    async with manager_cache.async_http_client() as client:

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")
        
        async with client.stream("POST", url, content=b"x") as r1:
            assert r1.headers.get("x-cache") != "HIT" and r1.extensions.get("from_cache") is not True

        async with client.stream("POST", url, content=b"x") as r2:
            assert r2.headers.get("x-cache") != "HIT" and r2.extensions.get("from_cache") is not True

    assert calls == 2
