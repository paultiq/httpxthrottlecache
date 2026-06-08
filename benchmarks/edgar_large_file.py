import logging
import time

import boto3
from botocore.config import Config

from httpxthrottlecache import EDGAR_CACHE_RULES, HttpxThrottleCache

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)-8s %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)

logging.getLogger("botocore").setLevel(logging.INFO)
USER_AGENT = "Iq Te teiq@iqmo.com"

TEST_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
TEST_URL = "https://www.sec.gov/files/company_tickers.json"

CACHE_DIR = "_fccache"
s3 = boto3.client(
    "s3",
    config=Config(
        max_pool_connections=50,
        tcp_keepalive=True,
    ),
)

BUCKET = "large-cache-test"


def clear_cache():
    if CACHE_DIR.exists():
        for f in CACHE_DIR.iterdir():
            f.unlink()


def test_single_no_cache():
    start = time.perf_counter()

    with HttpxThrottleCache(user_agent=USER_AGENT, cache_mode=False) as htc:
        with htc.http_client() as client:
            client.get(url=TEST_URL)

    end = time.perf_counter()

    logger.info("Duration: %s", end - start)


def test_single_hits():
    with HttpxThrottleCache(user_agent=USER_AGENT, cache_dir=CACHE_DIR, cache_rules=EDGAR_CACHE_RULES) as htc:
        with htc.http_client() as client:
            start = time.perf_counter()
            client.get(url=TEST_URL)
            end = time.perf_counter()
            logger.info("Duration: %s", end - start)

            start = time.perf_counter()
            client.get(url=TEST_URL)
            end = time.perf_counter()
            logger.info("Duration: %s", end - start)


def test_single_hit_boto3():
    # s3.create_bucket(Bucket=bucket)

    start = time.perf_counter()

    with HttpxThrottleCache(
        user_agent=USER_AGENT, cache_dir=CACHE_DIR, s3_bucket=BUCKET, s3_client=s3, cache_rules=EDGAR_CACHE_RULES
    ) as htc:
        with htc.http_client() as client:
            response1 = client.get(url=TEST_URL)
            end = time.perf_counter()
            logger.info("Duration: %s", end - start)

            start = time.perf_counter()
            response2 = client.get(url=TEST_URL)
            end = time.perf_counter()
            logger.info("Duration: %s", end - start)

            assert response1.headers["date"] == response2.headers["date"]


if __name__ == "__main__":
    # logger.info("Testing with no cache, plain vanilla HTTPX")
    # test_edgar_no_cache()

    # logger.info("Wiping cache")
    # clear_cache()

    # logger.info("Testing first hit: JSONSerializer")
    # test_edgar_hishel_transport(serializer=hishel.JSONSerializer())

    # logger.info("Testing second hit: JSONSerializer")
    # test_edgar_hishel_transport(serializer=hishel.JSONSerializer())

    # logger.info("Wiping cache")
    # clear_cache()

    # logger.info("Testing first hit: JSONByteSerializer")
    # test_edgar_hishel_transport(serializer=hishel.JSONByteSerializer())
    test_single_no_cache()
    test_single_hits()

    test_single_hit_boto3()
