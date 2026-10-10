import email.utils
import time

from httpxthrottlecache import httpx
from httpxthrottlecache.filecache.transport import CachingTransport, FileCache

LONG_QUERY = "&".join(f"param_{i}=value_{i}_" + "x" * 20 for i in range(15))


def test_long_url_name_is_bounded(tmp_path):
    p = FileCache(tmp_path, locking=False).to_path("a.test", "/feed", LONG_QUERY)
    assert len(p.name) <= 150


def test_long_urls_stay_distinct(tmp_path):
    fc = FileCache(tmp_path, locking=False)
    assert fc.to_path("a.test", "/feed", LONG_QUERY + "1") != fc.to_path("a.test", "/feed", LONG_QUERY + "2")


def test_long_url_is_cached(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url)
        headers = {"Date": email.utils.formatdate(time.time(), usegmt=True)}
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"body"), request=req)

    transport = CachingTransport(tmp_path, {".*": {".*": 3600}}, transport=httpx.MockTransport(handler))
    url = f"https://a.test/feed?{LONG_QUERY}"
    with httpx.Client(transport=transport) as c:
        assert c.get(url).content == b"body"
        assert c.get(url).content == b"body"
    assert len(calls) == 1
