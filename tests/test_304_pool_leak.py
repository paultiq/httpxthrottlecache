import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from httpxthrottlecache import httpx
from httpxthrottlecache.filecache.transport import CachingTransport

LM = "Wed, 01 Jan 2024 00:00:00 GMT"
ETAG = '"v1"'
_hits: list[int] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.headers.get("If-None-Match") or self.headers.get("If-Modified-Since"):
            _hits.append(304)
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.send_header("Last-Modified", LM)
            self.end_headers()
        else:
            _hits.append(200)
            body = b"hello\n"
            self.send_response(200)
            self.send_header("ETag", ETAG)
            self.send_header("Last-Modified", LM)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class _Server:
    def __init__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}/doc.txt"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def loopback(tmp_path):
    _hits.clear()
    srv = _Server()
    try:
        yield srv
    finally:
        srv.close()
        for meta_ext in ".meta", ".tmp", ".lock":
            for p in tmp_path.glob(f"**/*.{meta_ext}"):
                p.unlink(missing_ok=True)


def test_304_revalidation_does_not_leak_pool_connection(loopback, tmp_path):
    """Ensure 304's close per #60"""
    net = httpx.HTTPTransport(
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=2)
    )
    transport = CachingTransport(
        cache_dir=tmp_path,
        cache_rules={r"127\.0\.0\.1": {r".*": -1}},
        transport=net,
    )
    client = httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(5.0, pool=1.0),
    )
    try:
        for i in range(1, 6):
            r = client.get(loopback.url)
            assert r.status_code in (200, 304), f"req {i} status={r.status_code}"
        busy = [c for c in net._pool.connections if not c.is_idle()]
        assert not busy, f"pool connections leaked: {len(busy)}"
    finally:
        client.close()
