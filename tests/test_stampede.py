import pytest
import httpx2
from httpx2 import Response
import email
import time
import asyncio
from httpxthrottlecache import HttpxThrottleCache
import logging

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_cache_stampede(manager_cache: HttpxThrottleCache, monkeypatch):
    calls =0 
    url = "https://example.com/file.bin"
    ttl = 1
    manager_cache.cache_rules = {"example.com": {"/file.bin": 5}}  # force stale without monkeypatching time

    class _Chunks(httpx2.AsyncByteStream): 
        def __init__(self, cs): 
            self.cs=cs

        async def __aiter__(self): 
            for c in self.cs: 
                yield c

        async def aclose(self): 
            pass

    def handler(req):
        nonlocal calls; calls += 1
        return Response(200, headers={
            "Content-Length": "3",
            "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
            "Date": email.utils.formatdate(usegmt=True),
        }, stream=_Chunks([b"a", b"b", b"c"]), request=req)
    
    async with manager_cache.async_http_client() as client:
        
        mock = httpx2.MockTransport(handler)
        if hasattr(client._transport, "transport"): 
            client._transport.transport = mock
        elif hasattr(client._transport, "_transport"): 
            client._transport._transport = mock
        t0 = time.time()

        N = 200
        resps = await asyncio.gather(*(client.get(url) for _ in range(N)))
        misses = sum(r.headers.get("x-cache") == "MISS"  or r.extensions.get("from_cache") == False for r in resps)
        hits = sum(r.headers.get("x-cache") == "HIT"  or r.extensions.get("from_cache") == True for r in resps)


        logger.warning("Stampede results in many misses: calls=%s, misses=%s, hits=%s", calls, misses, hits)                 
        logger.warning("This is not optimal")

def test_cache_notreallystampede_warmed_first(manager_cache: HttpxThrottleCache, monkeypatch):
    calls =0 
    url = "https://example.com/file.bin"
    ttl = 1
    manager_cache.cache_rules = {"example.com": {"/file.bin": 5}}  # force stale without monkeypatching time

    class _Chunks(httpx2.ByteStream): 
        def __init__(self, cs): 
            self.cs=cs
            super().__init__(self)

        def __iter__(self): 
            for c in self.cs: 
                yield c

        def close(self): 
            pass

    def handler(req):
        nonlocal calls; calls += 1
        return Response(200, headers={
            "Content-Length": "3",
            "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
            "Date": email.utils.formatdate(usegmt=True),
        }, stream=_Chunks([b"a", b"b", b"c"]), request=req)
    
    with manager_cache.http_client() as client:
        
        mock = httpx2.MockTransport(handler)
        if hasattr(client._transport, "transport"): 
            client._transport.transport = mock
        elif hasattr(client._transport, "_transport"): 
            client._transport._transport = mock
        r1 = client.get(url)

        t0 = time.time()

        n = 200
        resps = [client.get(url) for _ in range(n)]
        misses = sum(r.headers.get("x-cache") == "MISS"  or r.extensions.get("from_cache") == False for r in resps)
        hits = sum(r.headers.get("x-cache") == "HIT"  or r.extensions.get("from_cache") == True for r in resps)

        assert misses == 0
        assert hits == n

@pytest.mark.asyncio
async def test_cache_different_requests(manager_cache: HttpxThrottleCache, monkeypatch):
    calls =0 
    manager_cache.cache_rules = {"example.com": {"/file.bin": 5}}  # force stale without monkeypatching time
    url = "https://example.com/file.bin"

    class _Chunks(httpx2.AsyncByteStream): 
        def __init__(self, cs): 
            self.cs=cs

        async def __aiter__(self): 
            for c in self.cs: 
                yield c

        async def aclose(self): 
            pass

    def handler(req):
        nonlocal calls; calls += 1
        return Response(200, headers={
            "Content-Length": "3",
            "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
            "Date": email.utils.formatdate(usegmt=True),
        }, stream=_Chunks([b"a", b"b", b"c"]), request=req)
    
    async with manager_cache.async_http_client() as client:
        
        mock = httpx2.MockTransport(handler)
        if hasattr(client._transport, "transport"): 
            client._transport.transport = mock
        elif hasattr(client._transport, "_transport"): 
            client._transport._transport = mock
        t0 = time.time()

        n = 200
        resps = await asyncio.gather(*(client.get(f"{url}_{i}") for i in range(n)))
        misses = sum(r.headers.get("x-cache") == "MISS"  or r.extensions.get("from_cache") == False for r in resps)
        hits = sum(r.headers.get("x-cache") == "HIT"  or r.extensions.get("from_cache") == True for r in resps)


        logger.warning("Stampede results in many misses: calls=%s, misses=%s, hits=%s", calls, misses, hits)                 
