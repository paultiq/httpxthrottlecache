import time


import time
import asyncio
import pytest
import logging
from httpxthrottlecache import httpx
from httpxthrottlecache import HttpxThrottleCache
from conftest import mock_client
logger=logging.getLogger(__name__)

SMALL_URL = "https://www.sec.gov/Archives/edgar/data/51143/000155837021009351/ibm-20210630ex32157e1d5.htm"
TEST_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

def test_file_sync(manager_cache: HttpxThrottleCache):
    """Make two requests, make sure they succeed, and make sure the dates are the same: showing the cache hit worked"""


    with manager_cache.http_client() as client:
        response = client.get(url=SMALL_URL)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    
    with manager_cache.http_client() as client:
        response = client.get(url=SMALL_URL)

        second_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    assert first_date == second_date

    # Make 30 requests, they should complete in under a second because caching --magic--
    
    with manager_cache.http_client() as client:
        start = time.perf_counter()
        responses = [client.get(url=SMALL_URL) for _ in range(30)]
        end = time.perf_counter()

        assert responses[0].headers["date"] == responses[-1].headers["date"]

        codes = [r.status_code for r in responses]
        assert min(codes) == 200 and max(codes) == 200

    assert (end-start) < 1

def test_file_sync_nocache(manager_nocache: HttpxThrottleCache):
    """Make two requests, make sure they succeed, and make sure the dates are the same: showing the cache hit worked"""


    with manager_nocache.http_client() as client:
        response = client.get(url=SMALL_URL)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    with manager_nocache.http_client() as client:
        r2 = client.get(url=SMALL_URL)

        second_date = r2.headers["date"]
        assert r2.status_code == 200, r2.status_code

    assert r2.headers.get("x-cache") is None and r2.extensions.get("from_cache") is None

    # Make 30 requests, they should complete in under a second because caching magic
    
    with manager_nocache.http_client() as client:
        start = time.perf_counter()
        responses = [client.get(url=SMALL_URL).status_code for _ in range(30)]
        end = time.perf_counter()
    
    assert min(responses) == 200 and max(responses) == 200
    assert (end-start) > 3

@pytest.mark.asyncio
async def test_file_async(manager_cache: HttpxThrottleCache):
    """Make two requests, make sure they succeed, and make sure the dates are the same: showing the cache hit worked"""


    async with manager_cache.async_http_client() as client:
        response = await client.get(url=SMALL_URL)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    
    async with manager_cache.async_http_client() as client:
        response = await client.get(url=SMALL_URL)

        second_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    assert first_date == second_date

    # Make 30 requests, they should complete in under a second because caching magic
    
    async with manager_cache.async_http_client() as client:
        start = time.perf_counter()

        tasks = [client.get(url=SMALL_URL) for _ in range(30)]
        results = await asyncio.gather(*tasks)
        responses = [r.status_code for r in results] 
        end = time.perf_counter()
    
    assert min(responses) == 200 and max(responses) == 200
    assert (end-start) < 1


@pytest.mark.asyncio
async def test_file_async_nocache(manager_nocache: HttpxThrottleCache):
    """Make two requests, make sure they succeed, and make sure the dates are the same: showing the cache hit worked"""
    

    async with manager_nocache.async_http_client() as client:
        mock_client(client)
        response = await client.get(url=SMALL_URL)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    async with manager_nocache.async_http_client() as client:
        mock_client(client)
        r2 = await client.get(url=SMALL_URL)

        second_date = r2.headers["date"]
        assert r2.status_code == 200, r2.status_code
        logger.info(r2.headers)
        assert r2.headers.get("x-cache") is None and r2.extensions.get("from_cache") is None

    # Make 30 requests, they should complete in under a second because caching magic
    
    async with manager_nocache.async_http_client() as client:
        start = time.perf_counter()

        tasks = [client.get(url=SMALL_URL) for _ in range(15)]
        results = await asyncio.gather(*tasks)
        responses = [r.status_code for r in results] 
        end = time.perf_counter()
    
    assert min(responses) == 200 and max(responses) == 200
    assert (end-start) > 1

    # Change the rate limit and check again
    manager_nocache.update_rate_limiter(requests_per_second=5)
    

    async with manager_nocache.async_http_client() as client:
        
        # warm the rate bucket
        tasks = [client.get(url=SMALL_URL) for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        start = time.perf_counter()
        tasks = [client.get(url=SMALL_URL) for _ in range(12)]
        results = await asyncio.gather(*tasks)
        responses = [r.status_code for r in results] 
        end = time.perf_counter()
        
        assert min(responses) == 200 and max(responses) == 200
        assert (end-start) > 2 and (end-start) < 6
    
def test_short_cache_edgar_url(manager_cache: HttpxThrottleCache):
    url = "https://www.sec.gov/files/company_tickers.json"

    with manager_cache.http_client() as client:
        response = client.get(url=url)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    with manager_cache.http_client() as client:
        r2 = client.get(url=url)

        second_date = r2.headers["date"]
        assert r2.status_code == 200, response.status_code

    assert r2.headers.get("x-cache") == "HIT" or r2.extensions.get("from_cache") == True


def test_non_edgar_url(manager_cache: HttpxThrottleCache):
    """Make two requests, make sure they succeed, and make sure the dates are the same: showing the cache hit worked"""

    url = "https://httpbingo.org/get"
    with manager_cache.http_client() as client:
        response = client.get(url=url)

        first_date = response.headers["date"]
        assert response.status_code == 200, response.status_code

    with manager_cache.http_client() as client:
        r2 = client.get(url=url)

        second_date = response.headers["date"]
        assert r2.status_code == 200, r2.status_code

        assert (r2.headers.get("x-cache") == "MISS" or r2.headers.get("x-cache") is None) or (r2.extensions.get("from_cache") is None or r2.extensions.get("from_cache") == False)

    # Not cached


def test_close(manager_cache: HttpxThrottleCache):

    url = "https://httpbingo.org/get"
    with manager_cache.http_client() as client:
        response = client.get(url=url)

        assert response.status_code == 200, response.status_code

    manager_cache.close()

    with manager_cache.http_client() as client:
        response = client.get(url=url)

        assert response.status_code == 200, response.status_code
