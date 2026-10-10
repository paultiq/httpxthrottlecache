import email.utils
import time

import pytest

from httpxthrottlecache import httpx
from httpxthrottlecache.filecache.transport import CachingTransport

RULES = {".*": {".*": 3600}}


def _handler(calls):
    def handler(req):
        calls.append(str(req.url))
        date = email.utils.formatdate(time.time(), usegmt=True)
        if req.url.path == "/old":
            return httpx.Response(301, headers={"Location": "/new", "Date": date}, request=req)
        if req.url.path == "/temp":
            return httpx.Response(302, headers={"Location": "/new", "Date": date}, request=req)
        headers = {"Date": date, "content-type": "text/plain"}
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"body"), request=req)

    return handler


def test_301_is_served_from_cache(tmp_path):
    calls = []
    transport = CachingTransport(tmp_path, RULES, transport=httpx.MockTransport(_handler(calls)))
    with httpx.Client(transport=transport) as c:
        assert c.get("https://a.test/old", follow_redirects=True).content == b"body"
        r = c.get("https://a.test/old", follow_redirects=True)
        assert r.content == b"body"
        assert str(r.url) == "https://a.test/new"
        assert r.history[0].status_code == 301
        assert r.history[0].headers["x-cache"] == "HIT"
    assert calls == ["https://a.test/old", "https://a.test/new"]


def test_cached_301_without_following(tmp_path):
    calls = []
    transport = CachingTransport(tmp_path, RULES, transport=httpx.MockTransport(_handler(calls)))
    with httpx.Client(transport=transport) as c:
        c.get("https://a.test/old")
        r = c.get("https://a.test/old")
    assert r.status_code == 301
    assert r.headers["location"] == "/new"
    assert len(calls) == 1


def test_stale_301_is_refetched(tmp_path, monkeypatch):
    calls = []
    transport = CachingTransport(tmp_path, {".*": {".*": 10}}, transport=httpx.MockTransport(_handler(calls)))
    t0 = time.time()
    with httpx.Client(transport=transport) as c:
        c.get("https://a.test/old")
        monkeypatch.setattr(time, "time", lambda: t0 + 60)
        r = c.get("https://a.test/old")
    assert r.status_code == 301
    assert len(calls) == 2


def test_302_is_cached(tmp_path):
    calls = []
    transport = CachingTransport(tmp_path, RULES, transport=httpx.MockTransport(_handler(calls)))
    with httpx.Client(transport=transport) as c:
        c.get("https://a.test/temp")
        r = c.get("https://a.test/temp")
    assert r.status_code == 302
    assert r.headers["location"] == "/new"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [307, 308])
def test_307_and_308_are_cached(tmp_path, status):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        date = email.utils.formatdate(time.time(), usegmt=True)
        return httpx.Response(status, headers={"Location": "/new", "Date": date}, request=req)

    transport = CachingTransport(tmp_path, RULES, transport=httpx.MockTransport(handler))
    with httpx.Client(transport=transport) as c:
        c.get("https://a.test/moved")
        r = c.get("https://a.test/moved")
    assert r.status_code == status
    assert r.headers["location"] == "/new"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_301_is_served_from_cache_async(tmp_path):
    calls = []
    transport = CachingTransport(tmp_path, RULES, transport=httpx.MockTransport(_handler(calls)))
    async with httpx.AsyncClient(transport=transport) as c:
        await c.get("https://a.test/old", follow_redirects=True)
        r = await c.get("https://a.test/old", follow_redirects=True)
    assert r.content == b"body"
    assert calls == ["https://a.test/old", "https://a.test/new"]
