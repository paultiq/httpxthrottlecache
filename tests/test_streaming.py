import httpx, pytest
from httpx import Response
from httpxthrottlecache import HttpxThrottleCache
import httpxthrottlecache
import os
import logging
logger=logging.getLogger(__name__)
import email.utils
import time 

@pytest.mark.asyncio
async def test_stream_updates_progress(manager_cache: HttpxThrottleCache, tmp_path, monkeypatch):
    url = "https://example.com/file.bin"
    manager_cache.cache_rules = {"example.com": {"/file.bin": 3600}}

    updates, calls = [], 0
    monkeypatch.setattr("tqdm.tqdm", lambda *a, **k: type("DummyTQDM", (), {
        "__init__": lambda self, *a, **k: None,
        "update": lambda self, 
        x: updates.append(x),
        "close": lambda self: None
    })())

    chunks = [b"x" * (512 * 1024)] * 4
    total = sum(map(len, chunks))
    expected = b"".join(chunks)
    out = tmp_path / "out.bin"

    class _Chunks(httpx.AsyncByteStream):
        def __init__(self, cs): self._cs = cs
        async def __aiter__(self):
            for c in self._cs:
                yield c
        async def aclose(self): 
            pass

    def handler(req):
        nonlocal calls
        calls += 1
        return Response(
            200,
            headers={
                "Content-Length": str(total),
                "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                "Date": email.utils.formatdate(usegmt=True)
            },
            stream=_Chunks(chunks),
            request=req
        )

    async with manager_cache.async_http_client() as client:
        next_transport = httpx.MockTransport(handler)

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")

        async with client.stream("GET", url) as resp:
            with open(out, "wb") as f:
                async for ck in resp.aiter_bytes(chunk_size=256 * 1024):
                    f.write(ck)
                    updates.append(len(ck))

        assert out.exists()
        assert out.read_bytes() == expected
        assert len(updates) > 2
        assert sum(updates) == total

        # second request - should be cache HIT
        async with client.stream("GET", url) as resp2:
            assert resp2.headers.get("x-cache") == "HIT" or resp2.extensions.get("from_cache") == True
            async for _ in resp2.aiter_bytes():
                pass

    assert calls == 1

@pytest.mark.asyncio
async def test_cache_stale_is_not_hit(manager_cache, tmp_path, monkeypatch):
    url, calls = "https://example.com/file.bin", 0
    start = time.time()
    class _Chunks(httpx.AsyncByteStream):
        def __init__(self, cs): 
            self.cs=cs
        async def __aiter__(self):
            for c in self.cs: yield c
        async def aclose(self): 
            pass
    def handler(req):
        nonlocal calls
        calls += 1
        return Response(200, headers={
            "Content-Length":"2",
            "Last-Modified":"Mon, 01 Jan 2024 00:00:00 GMT",
            "Date": email.utils.formatdate(start - 2*3600, usegmt=True),  # 2h old
        }, stream=_Chunks([b"x", b"y"]), request=req)

    manager_cache.cache_rules = {"example.com": {"/file.bin": 3600}}
    async with manager_cache.async_http_client() as client:
        mt = httpx.MockTransport(handler)
        if hasattr(client._transport, "transport"): 
            client._transport.transport = mt
        elif hasattr(client._transport, "_transport"): 
            client._transport._transport = mt

        async with client.stream("GET", url): 
            pass  # populate cache
        monkeypatch.setattr(time, "time", lambda: start + 2*3600)  # make FileCache stale too

        async with client.stream("GET", url) as resp2:
            assert resp2.headers.get("x-cache") != "HIT" and resp2.extensions.get("from_cache") is not True


def test_stream_updates_progress_sync(manager_cache: HttpxThrottleCache, tmp_path, monkeypatch):
    url = "https://example.com/file.bin"
    manager_cache.cache_rules = {"example.com": {"/file.bin": 3600}}

    updates, calls = [], 0
    monkeypatch.setattr("tqdm.tqdm", lambda *a, **k: type("DummyTQDM", (), {
        "__init__": lambda self, *a, **k: None,
        "update": lambda self, 
        x: updates.append(x),
        "close": lambda self: None
    })())

    chunks = [b"x" * (512 * 1024)] * 4
    total = sum(map(len, chunks))
    expected = b"".join(chunks)
    out = tmp_path / "out.bin"

    class _Chunks(httpx.ByteStream):
        def __init__(self, cs): 
            self._cs = cs
            super().__init__(self)

        def __iter__(self):
            for c in self._cs:
                yield c
        def close(self): 
            pass

    def handler(req):
        nonlocal calls
        calls += 1
        return Response(
            200,
            headers={
                "Content-Length": str(total),
                "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                "Date": email.utils.formatdate(usegmt=True)
            },
            stream=_Chunks(chunks),
            request=req
        )

    with manager_cache.http_client() as client:
        next_transport = httpx.MockTransport(handler)

        if isinstance(client._transport, httpxthrottlecache.filecache.transport.CachingTransport):
            client._transport.transport = next_transport
        else:
            raise AssertionError(f"Unexpected transport type: {type(client._transport)}")

        with client.stream("GET", url) as resp:
            with open(out, "wb") as f:
                for ck in resp.iter_bytes(chunk_size=256 * 1024):
                    f.write(ck)
                    updates.append(len(ck))

        assert out.exists()
        assert out.read_bytes() == expected
        assert len(updates) > 2
        assert sum(updates) == total

        # second request - should be cache HIT
        with client.stream("GET", url) as resp2:
            assert resp2.headers.get("x-cache") == "HIT" or resp2.extensions.get("from_cache") == True
            for _ in resp2.iter_bytes():
                pass

    assert calls == 1

def test_cache_stale_is_not_hit_sync(manager_cache, tmp_path, monkeypatch):
    url, calls = "https://example.com/file.bin", 0
    start = time.time()

    class _Chunks(httpx.ByteStream):
        def __init__(self, cs): 
            self.cs=cs
            super().__init__(self)
        def __iter__(self):
            for c in self.cs: yield c
        def close(self): 
            pass
    
    def handler(req):
        nonlocal calls
        calls += 1
        return Response(200, headers={
            "Content-Length":"2",
            "Last-Modified":"Mon, 01 Jan 2024 00:00:00 GMT",
            "Date": email.utils.formatdate(start - 2*3600, usegmt=True),  # 2h old
        }, stream=_Chunks([b"x", b"y"]), request=req)

    manager_cache.cache_rules = {"example.com": {"/file.bin": 3600}}
    with manager_cache.http_client() as client:
        mt = httpx.MockTransport(handler)
        if hasattr(client._transport, "transport"): 
            client._transport.transport = mt
        elif hasattr(client._transport, "_transport"): 
            client._transport._transport = mt

        with client.stream("GET", url): 
            pass  # populate cache
        monkeypatch.setattr(time, "time", lambda: start + 2*3600)  # make FileCache stale too

        with client.stream("GET", url) as resp2:
            assert resp2.headers.get("x-cache") != "HIT" and resp2.extensions.get("from_cache") is not True