import datetime
import email.utils
import importlib.machinery
import importlib.util
import sys
import time
import types

import pytest

import httpxthrottlecache.ratelimiter
from httpxthrottlecache import HttpxThrottleCache, httpx
from httpxthrottlecache.filecache.transport import CachingTransport

DATE = "Mon, 01 Jan 2024 00:00:00 GMT"


class CountingLimiter:
    def __init__(self):
        self.items = []

    def try_acquire(self, item):
        self.items.append(item)

    async def try_acquire_async(self, item):
        self.items.append(item)


def _make_fake_primp():
    mod = types.ModuleType("primp")
    mod.__spec__ = importlib.machinery.ModuleSpec("primp", None)

    class PrimpError(Exception): ...
    class RequestError(PrimpError): ...
    class ConnectError(RequestError): ...
    class TimeoutError(RequestError): ...  # noqa: A001
    class DNSError(RequestError): ...
    class BodyError(PrimpError): ...
    class DecodeError(PrimpError): ...

    class Response:
        def __init__(self, status_code=200, headers=None, chunks=(b"ok",), error=None):
            self.status_code = status_code
            self.headers = {"date": DATE, **(headers or {})}
            self.chunks, self.error, self.closed = chunks, error, False

        def iter_bytes(self):
            yield from self.chunks
            if self.error:
                raise self.error

        async def _agen(self):
            for c in self.chunks:
                yield c
            if self.error:
                raise self.error

        def aiter_bytes(self):
            return self._agen()

        def close(self):
            self.closed = True

        async def aclose(self):
            self.closed = True

    class Client:
        def __init__(self, **kwargs):
            mod.inits.append(kwargs)

        def request(self, method, url, **kwargs):
            mod.calls.append((method, url, kwargs))
            return mod.handler(method, url, kwargs)

    class AsyncClient(Client):
        async def request(self, method, url, **kwargs):
            return Client.request(self, method, url, **kwargs)

    for cls in (PrimpError, RequestError, ConnectError, TimeoutError, DNSError, BodyError, DecodeError, Response, Client, AsyncClient):
        setattr(mod, cls.__name__, cls)
    mod.AsyncResponse = Response
    mod.inits, mod.calls = [], []
    mod.handler = lambda method, url, kwargs: Response()
    return mod


@pytest.fixture(name="fake_primp")
def _fake_primp(monkeypatch):
    mod = _make_fake_primp()
    monkeypatch.setitem(sys.modules, "primp", mod)
    monkeypatch.delitem(sys.modules, "httpxthrottlecache.primp_transport", raising=False)
    yield mod
    # The module was imported against the fake; drop it so later tests import the real primp.
    sys.modules.pop("httpxthrottlecache.primp_transport", None)


@pytest.fixture(name="limiter")
def _limiter():
    return CountingLimiter()


def _mgr(limiter, tmp_path=None, **kwargs):
    cache = {"cache_mode": "FileCache", "cache_dir": tmp_path} if tmp_path else {"cache_mode": "Disabled"}
    return HttpxThrottleCache(rate_limiter=limiter, backend="primp", **cache, **kwargs)


def test_missing_primp_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "primp", None)
    with pytest.raises(ImportError, match="httpxthrottlecache\\[primp\\]"):
        HttpxThrottleCache(cache_mode="Disabled", backend="primp")


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="Unsupported backend"):
        HttpxThrottleCache(cache_mode="Disabled", backend="curl")


def test_default_backend_unchanged(monkeypatch):
    monkeypatch.setitem(sys.modules, "primp", None)
    mgr = HttpxThrottleCache(cache_mode="Disabled", primp_params={"impersonate": "chrome"})
    assert mgr.backend == "httpx"
    with mgr.http_client() as client:
        assert isinstance(client._transport, httpxthrottlecache.ratelimiter.RateLimitingTransport)


def test_default_primp_params_not_shared():
    a, b = HttpxThrottleCache(cache_mode="Disabled"), HttpxThrottleCache(cache_mode="Disabled")
    assert a.primp_params == {"impersonate": "firefox"}
    assert a.primp_params is not b.primp_params


def test_client_params(fake_primp, limiter):
    with _mgr(limiter).http_client():
        pass
    assert fake_primp.inits == [{"cookie_store": False, "impersonate": "firefox", "follow_redirects": False, "verify": True}]


def test_follow_redirects_cannot_be_overridden(fake_primp, limiter):
    with _mgr(limiter, primp_params={"follow_redirects": True}).http_client():
        pass
    assert fake_primp.inits[0]["follow_redirects"] is False


def test_request_translation(fake_primp, limiter):
    fake_primp.handler = lambda m, u, kw: fake_primp.Response(
        headers={"content-type": "text/plain", "content-encoding": "gzip", "content-length": "99", "x-a": "1"},
        chunks=(b"he", b"llo"),
    )
    mgr = _mgr(limiter, user_agent="ua/1.0", httpx_params={"headers": {"sec-ch-ua": "x"}, "timeout": 7})
    with mgr.http_client() as client:
        r = client.get("https://example.com/p?q=1", headers={"X-Custom": "v"})

    method, url, kw = fake_primp.calls[0]
    assert (method, url) == ("GET", "https://example.com/p?q=1")
    assert kw["headers"]["x-custom"] == "v"
    for h in ("user-agent", "sec-ch-ua", "accept-encoding", "host", "connection"):
        assert h not in kw["headers"]
    assert kw["timeout"] == 7
    assert kw["follow_redirects"] is False
    assert r.content == b"hello"
    assert r.headers["x-a"] == "1"
    assert "content-encoding" not in r.headers
    assert limiter.items == [httpxthrottlecache.ratelimiter.__name__]


def test_post_body_forwarded(fake_primp, limiter):
    with _mgr(limiter).http_client() as client:
        client.post("https://example.com/p", content=b"payload")
    assert fake_primp.calls[0][2]["content"] == b"payload"


def test_each_redirect_hop_hits_limiter(fake_primp, limiter):
    def handler(m, u, kw):
        if u.endswith("/a"):
            return fake_primp.Response(302, headers={"location": "https://example.com/b"})
        return fake_primp.Response(chunks=(b"final",))

    fake_primp.handler = handler
    with _mgr(limiter).http_client() as client:
        r = client.get("https://example.com/a", follow_redirects=True)
    assert r.content == b"final"
    assert [c[1] for c in fake_primp.calls] == ["https://example.com/a", "https://example.com/b"]
    assert len(limiter.items) == 2


@pytest.mark.parametrize(
    "primp_exc, httpx_exc",
    [("TimeoutError", httpx.TimeoutException), ("ConnectError", httpx.ConnectError), ("DNSError", httpx.ConnectError), ("PrimpError", httpx.TransportError)],
)
def test_error_mapping(fake_primp, limiter, primp_exc, httpx_exc):
    def handler(m, u, kw):
        raise getattr(fake_primp, primp_exc)("boom")

    fake_primp.handler = handler
    with _mgr(limiter).http_client() as client:
        with pytest.raises(httpx_exc):
            client.get("https://example.com/")


def test_body_error_mapping(fake_primp, limiter):
    fake_primp.handler = lambda m, u, kw: fake_primp.Response(error=fake_primp.BodyError("cut"))
    with _mgr(limiter).http_client() as client:
        with pytest.raises(httpx.ReadError):
            client.get("https://example.com/")


def test_cache_miss_then_hit(fake_primp, limiter, tmp_path):
    lm = "Sun, 31 Dec 2023 00:00:00 GMT"
    fake_primp.handler = lambda m, u, kw: fake_primp.Response(
        headers={"last-modified": lm, "content-type": "text/plain"}, chunks=(b"cached", b"body")
    )
    mgr = _mgr(limiter, tmp_path, cache_rules={"example.com": {"/f": True}})
    with mgr.http_client() as client:
        assert isinstance(client._transport, CachingTransport)
        r1 = client.get("https://example.com/f")
        r2 = client.get("https://example.com/f")
    assert (r1.headers["x-cache"], r2.headers["x-cache"]) == ("MISS", "HIT")
    assert r1.content == r2.content == b"cachedbody"
    assert r2.headers["Date"] == DATE
    assert r2.headers["Last-Modified"] == lm
    assert len(fake_primp.calls) == 1
    assert len(limiter.items) == 1


def test_304_revalidation(fake_primp, limiter, tmp_path, monkeypatch):
    dt = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    t0 = dt.timestamp()
    lm = email.utils.format_datetime(dt, usegmt=True)
    monkeypatch.setattr(time, "time", lambda: t0)

    def handler(m, u, kw):
        if len(fake_primp.calls) == 1:
            return fake_primp.Response(headers={"last-modified": lm, "date": lm}, chunks=(b"abc",))
        return fake_primp.Response(304, headers={"last-modified": lm, "date": lm}, chunks=())

    fake_primp.handler = handler
    mgr = _mgr(limiter, tmp_path, cache_rules={"example.com": {"/f": 1}})
    with mgr.http_client() as client:
        r1 = client.get("https://example.com/f")
        monkeypatch.setattr(time, "time", lambda: t0 + 5)
        r2 = client.get("https://example.com/f")
    assert fake_primp.calls[1][2]["headers"]["if-modified-since"] == lm
    assert r2.status_code == 304
    assert r2.headers["x-cache"] == "HIT"
    assert r1.content == r2.content == b"abc"


def test_update_rate_limiter_applies_to_next_client(fake_primp, limiter):
    mgr = _mgr(limiter)
    mgr.update_rate_limiter(5)
    with mgr.http_client() as client:
        assert client._transport.limiter is mgr.rate_limiter


def test_rate_limiter_disabled(fake_primp, limiter):
    mgr = HttpxThrottleCache(cache_mode="Disabled", rate_limiter_enabled=False, backend="primp")
    with mgr.http_client() as client:
        assert client._transport.limiter is None
        client.get("https://example.com/")


async def test_async_translation_and_cache(fake_primp, limiter, tmp_path):
    fake_primp.handler = lambda m, u, kw: fake_primp.Response(
        headers={"last-modified": DATE, "content-encoding": "br"}, chunks=(b"as", b"ync")
    )
    mgr = _mgr(limiter, tmp_path, cache_rules={"example.com": {"/f": True}}, httpx_params={"timeout": 3})
    async with mgr.async_http_client() as client:
        r1 = await client.get("https://example.com/f")
        r2 = await client.get("https://example.com/f")
    assert (r1.headers["x-cache"], r2.headers["x-cache"]) == ("MISS", "HIT")
    assert r1.content == r2.content == b"async"
    assert "content-encoding" not in r1.headers
    assert fake_primp.calls[0][2]["timeout"] == 3
    assert len(fake_primp.calls) == 1
    assert limiter.items == [httpxthrottlecache.ratelimiter.__name__]


async def test_async_error_mapping(fake_primp, limiter):
    def handler(m, u, kw):
        raise fake_primp.TimeoutError("slow")

    fake_primp.handler = handler
    async with _mgr(limiter).async_http_client() as client:
        with pytest.raises(httpx.TimeoutException):
            await client.get("https://example.com/")


async def test_async_body_error_mapping(fake_primp, limiter):
    fake_primp.handler = lambda m, u, kw: fake_primp.Response(error=fake_primp.DecodeError("bad gzip"))
    async with _mgr(limiter).async_http_client() as client:
        with pytest.raises(httpx.DecodingError):
            await client.get("https://example.com/")


@pytest.mark.skipif(importlib.util.find_spec("primp") is None, reason="primp not installed")
def test_real_primp_cache_roundtrip(tmp_path):
    mgr = HttpxThrottleCache(cache_dir=tmp_path, cache_rules={"example.com": {".*": True}}, backend="primp")
    with mgr.http_client() as client:
        r1 = client.get("https://example.com/")
        r2 = client.get("https://example.com/")
    assert r1.status_code == 200
    assert (r1.headers["x-cache"], r2.headers["x-cache"]) == ("MISS", "HIT")
    assert r1.content == r2.content
