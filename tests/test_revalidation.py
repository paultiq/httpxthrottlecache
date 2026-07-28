import asyncio
import email.utils
import time
from httpxthrottlecache._compat import httpx
import pytest
from httpxthrottlecache import HttpxThrottleCache
import datetime

Response = httpx.Response

@pytest.mark.asyncio
async def test_304_revalidate_serves_cached(manager_cache: HttpxThrottleCache, tmp_path, monkeypatch):
    calls, last_headers = 0, {}
    url = "https://example.com/file.bin" 

    manager_cache.cache_rules = {"example.com": {"/file.bin": 1}}
    dt = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    t0 = dt.timestamp()
    lm = email.utils.format_datetime(dt, usegmt=True)
    ttl = 1
    monkeypatch.setattr(time, "time", lambda: t0)
    body1 = b"abc"

    class _Chunks(httpx.AsyncByteStream):
        def __init__(self, b): self.b=b
        async def __aiter__(self): yield self.b
        async def aclose(self): pass

    def handler(req):
        nonlocal calls, last_headers
        calls += 1
        status = 200 if calls == 1 else 304
        return Response(status, headers={
            "Content-Length": str(len(body1)),
            "Last-Modified": lm,
            "Date": email.utils.formatdate(t0 if calls==1 else t0+ttl+1, usegmt=True),
        }, stream=_Chunks(body1), request=req)

    async with manager_cache.async_http_client() as client:
        mt = httpx.MockTransport(handler)
        setattr(client._transport, "transport" if hasattr(client._transport, "transport") else "_transport", mt)
        client._transport.cache_rules = manager_cache.cache_rules
        # 1) prime cache
        async with client.stream("GET", url) as r1: 
            assert r1.headers.get("x-cache") == "MISS" or r1.extensions.get("from_cache") == False
            await r1.aread()

        # Move time, so the data is now stale
        monkeypatch.setattr(time, "time", lambda: t0+ttl+2)

        # Time has moved so the data is outside the cache age, so we need a 304 revalidation
        async with client.stream("GET", url) as r2:
            assert r2.headers.get("x-cache") == "HIT" or r2.extensions.get("from_cache") == True
            await r2.aread()
        
            assert r2.status_code in (304, 200)

            assert r1.content==r2.content
        assert calls == 2  # second network round-trip for 304

@pytest.mark.asyncio
async def test_200_revalidate_refreshes_cache(manager_cache: HttpxThrottleCache, tmp_path, monkeypatch):
    """
        This tests requesting the same URL. The cache expires, then it's revalidated
        The revalidation changes so the new data is returned. 

        Two cache misses. 
    """
    calls = 0
    last_headers = {}
    url = "https://example.com/file.bin"

    manager_cache.cache_rules = {"example.com": {"/file.bin": 1}}
    dt1 = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    t0 = dt1.timestamp()
    lm1 = email.utils.format_datetime(dt1, usegmt=True)
    dt2 = datetime.datetime(2024, 1, 2, 0, 0, 0, tzinfo=datetime.timezone.utc)
    lm2 = email.utils.format_datetime(dt2, usegmt=True)

    ttl = 1
    monkeypatch.setattr(time, "time", lambda: t0)
    body1 = b"abc"
    body2 = b"DEF"

    class _Chunks(httpx.AsyncByteStream):
        def __init__(self, b): self.b=b
        async def __aiter__(self): yield self.b
        async def aclose(self): pass

    def handler(req):
        nonlocal calls, last_headers
        calls += 1
        last_headers = dict(req.headers)
        if calls == 1:
            return Response(200, headers={
                "Content-Length": str(len(body1)),
                "Last-Modified": lm1,
                "Date": email.utils.formatdate(t0, usegmt=True),
            }, stream=_Chunks(body1), request=req)
        else:
            return Response(200, headers={
                "Content-Length": str(len(body2)),
                "Last-Modified": lm2,
                "Date": email.utils.formatdate(t0+ttl+2, usegmt=True),
            }, stream=_Chunks(body2), request=req)

    async with manager_cache.async_http_client() as client:
        mt = httpx.MockTransport(handler)
        setattr(client._transport, "transport" if hasattr(client._transport, "transport") else "_transport", mt)

        # prime cache
        async with client.stream("GET", url) as r1:
             assert r1.headers.get("x-cache") == "MISS" or r1.extensions.get("from_cache") == False
             c1 = await r1.aread()
        assert c1 == body1
        # make stale
        monkeypatch.setattr(time, "time", lambda: t0+ttl+2)
        # revalidate -> 200 -> refresh + MISS
        async with client.stream("GET", url) as r2:
            assert r2.headers.get("x-cache") == "MISS" or r2.extensions.get("from_cache") == False
            c2 = await r2.aread()
            assert c2 == body2
            assert r2.status_code==200
        assert calls == 2


def test_304_revalidate_serves_cached_sync(manager_cache: HttpxThrottleCache, tmp_path, monkeypatch):
    calls, last_headers = 0, {}
    url = "https://example.com/file.bin" 

    manager_cache.cache_rules = {"example.com": {"/file.bin": 1}}
    dt = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    t0 = dt.timestamp()
    lm = email.utils.format_datetime(dt, usegmt=True)
    ttl = 1
    monkeypatch.setattr(time, "time", lambda: t0)
    body1 = b"abc"

    class _Chunks(httpx.ByteStream):
        def __init__(self, b): 
            self.b=b
            super().__init__(self)

        def __iter__(self): 
            yield self.b
            
        def close(self): 
            pass

    def handler(req):
        nonlocal calls, last_headers
        calls += 1
        status = 200 if calls == 1 else 304
        return Response(status, headers={
            "Content-Length": str(len(body1)),
            "Last-Modified": lm,
            "Date": email.utils.formatdate(t0 if calls==1 else t0+ttl+1, usegmt=True),
        }, stream=_Chunks(body1), request=req)

    with manager_cache.http_client() as client:
        mt = httpx.MockTransport(handler)
        setattr(client._transport, "transport" if hasattr(client._transport, "transport") else "_transport", mt)
        client._transport.cache_rules = manager_cache.cache_rules
        # 1) prime cache
        with client.stream("GET", url) as r1: 
            assert r1.headers.get("x-cache") == "MISS" or r1.extensions.get("from_cache") == False
            r1.read()
            
        # Move time, so the data is now stale
        monkeypatch.setattr(time, "time", lambda: t0+ttl+2)

        # Time has moved so the data is outside the cache age, so we need a 304 revalidation
        with client.stream("GET", url) as r2:
            assert r2.headers.get("x-cache") == "HIT" or r2.extensions.get("from_cache") == True
            r2.read()
        
            assert r2.status_code in (304, 200)

            assert r1.content==r2.content
        assert calls == 2  # second network round-trip for 304
