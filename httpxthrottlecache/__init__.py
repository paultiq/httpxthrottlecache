from ._compat import HTTPX_IMPL, httpx
from ._version import __version__
from .httpxclientmanager import HttpxThrottleCache

__all__ = ["HTTPX_IMPL", "HttpxThrottleCache", "__version__", "httpx"]


EDGAR_CACHE_RULES = {
    r".*\.sec\.gov": {
        "/api.*": 600,
        "/submissions.*": 600,
        r"/include/ticker\.txt.*": 600,
        r"/files/company_tickers\.json.*": 600,
        ".*index/.*": 1800,
        "/Archives/edgar/data": True,  # cache forever
    }
}
