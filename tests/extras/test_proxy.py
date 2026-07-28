# https://github.com/monokal/docker-tinyproxy
# docker run -d --name=tinyproxy -p 6666:8888 --env FilterDefaultDeny=No  monokal/tinyproxy:latest ANY
# curl -v --proxy http://127.0.0.1:6666 http://httpbingo.org/get
from httpxthrottlecache._compat import httpx
import os
import pytest
from httpxthrottlecache import HttpxThrottleCache
import logging

logger=logging.getLogger(__name__)

url = "http://httpbingo.org/get"

requires_proxy = pytest.mark.skipif(
    os.environ.get("HTTP_PROXY") is None,
    reason="HTTP_PROXY not set (needs a running tinyproxy, see header of this file)",
)


@requires_proxy
def test_proxy_http():

    with httpx.Client() as client:
        response = client.get(url)

        assert response.status_code == 200
        assert "tinyproxy" in response.headers.get("via")


@requires_proxy
def test_manager_proxy(manager_nocache: HttpxThrottleCache):

    with manager_nocache.http_client() as client:
        response = client.get(url)

        logger.info(f"{response.headers=}")
        assert response.status_code == 200
        assert "tinyproxy" in response.headers.get("via")
