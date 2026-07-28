HTTPX Wrapper with Rate Limiting and Caching Transports.

[![PyPI Version](https://badge.fury.io/py/httpxthrottlecache.svg)](https://pypi.python.org/pypi/httpxthrottlecache)
![Python Versions](https://img.shields.io/pypi/pyversions/httpxthrottlecache)

[![BuildRelease](https://github.com/paultiq/httpxthrottlecache/actions/workflows/build_deploy.yml/badge.svg)](https://github.com/paultiq/httpxthrottlecache/actions/workflows/build_deploy.yml)
[![Tests](https://github.com/paultiq/httpxthrottlecache/actions/workflows/test.yml/badge.svg)](https://github.com/paultiq/httpxthrottlecache/actions/workflows/test.yml)
[![Coverage badge](https://github.com/paultiq/httpxthrottlecache/raw/python-coverage-comment-action-data/badge.svg)](https://github.com/paultiq/httpxthrottlecache/tree/python-coverage-comment-action-data)


# Installation

httpxthrottlecache does not depend on an HTTP client directly. Install it with one of two extras, which selects the client:

```sh
pip install httpxthrottlecache[httpx2]  # recommended
```

or

```sh
pip install httpxthrottlecache[httpx]
```

[httpx2](https://pypi.org/project/httpx2/) is a maintained fork of httpx and is the recommended option. The `httpx` extra pins httpx 0.28.1, the final release of the original, no-longer-maintained package.

# Introduction

The goal of this project is a combination of convenience and as a demonstration of how to assemble HTTPX Transports in different combinations. 

HTTP request libraries, rate limiting and caching are topics with deep rabbit holes: the technical implementation details & the decisions an end user has to make. The convenience part of this package is abstracting away certain decisions and making certain opinionated decisions as to how caching & rate limiting should be controlled. 

This came about while implementing caching & rate limiting for [edgartools](https://edgartools.readthedocs.io/en/latest/): reducing network requests and improving overall performance led to a myriad of decisions. The SEC's Edgar site has a strict 
10 request per second limit, while providing not-very-helpful caching headers. Overriding these caching headers with custom rules is necessary in certain cases. 

# Caching

This project provides four `cache_mode` options:
- Disabled: Rate Limiting only
- Hishel-File: Cache using Hishel using FileStorage
- Hishel-S3: Cache using Hishel using S3Storage
- FileCache: Use a simpler filecache backend that uses file modified and created time and only revalidates using last-modified. For sites where last-modified is provided. 

Cache Rules are defined as a dictionary of site regular expressions to path regular expressions. 
```py
{
    'site_regex': {
        'url_regex': duration,
        'url_regex2': duration,
        '.*': 3600, # cache all paths for this site for an hour
    }
}
```

## Misc Settings:
- HTTPS_PROXY: HTTPS_PROXY environment variable is propagated to the HTTPX Transport

## Usage: Synchronous Requests

Note that the Manager object is intended to be long lived, doesn't need to be used as a context manager.

```py
from httpxthrottlecache import HttpxThrottleCache

url = "https://httpbingo.org/get"

with HttpxThrottleCache(cache_mode="Hishel-File", 
    cache_dir = "_cache", 
    rate_limiter_enabled=True, 
    request_per_sec_limit=10, 
    user_agent="your user agent") as manager:

    # Single synchronous request
    with manager.http_client() as client:
        response = client.get(url)
        print(response.status_code)
```

## Usage: Batch Requests
```py
from httpxthrottlecache import HttpxThrottleCache

url = "https://httpbingo.org/get"

with HttpxThrottleCache(cache_mode="Hishel-File", 
    cache_dir = "_cache", 
    rate_limiter_enabled=True, 
    request_per_sec_limit=10, 
    user_agent="your user agent") as manager:

# Batch request
responses = manager.get_batch([url,url])
print([r[0] for r in responses])
```

## Usage: Retrieve many files and write to files
```py
from pathlib import Path
from httpxthrottlecache import HttpxThrottleCache

with HttpxThrottleCache(cache_mode="Disabled") as mgr:
    urls = {f"https://httpbingo.org/get?{i}": Path(f"file{i}") for i in range(10)}
    results = mgr.get_batch(urls=urls)
```


## Usage: Asynchronous

```py
from httpxthrottlecache import HttpxThrottleCache
import asyncio 

url = "https://httpbingo.org/get"
with HttpxThrottleCache(cache_mode="Hishel-File", 
    cache_dir = "_cache", 
    rate_limiter_enabled=True, 
    request_per_sec_limit=10) as manager:

    # Async request
    async with manager.async_http_client() as client:
        tasks = [client.get(url) for _ in range(2)]
        responses = await asyncio.gather(*tasks)
        print(responses)
```

## FileCache

The FileCache implementation ignores response caching headers. Instead, it treats data as "fresh" for a client-provided max age. The max age is defined in a cacherule, as defined above.

Once the max age is expired, the FileCache Transport will revalidate the data using the Last-Modified date. TODO: Revalidate using ETAG as well. 

The FileCache implementation stores files as the raw bytes plus a .meta sidecar. The .meta provides headers, such as Last-Modified, which are used for revalidation. The raw bytes are in the native format - binary files are in their native format, compressed gzip streams are stored as compressed gzip data, etc. 

FileCache uses [FileLock](https://pypi.org/project/filelock/) to ensure only one writer to a cached object. This means that (currently) multiple simultaneous cache misses will stack up waiting to write to file. This locking is intended mainly to allow multiple processes to share the same cache. 

FileCache initially stages data to a .tmp file, then upon completion, copies to the final file. 

No cache cleanup is done - that's your problem.

# Rate Limiting

Rate limiting is implemented via [pyrate_limiter](https://pyratelimiter.readthedocs.io/en/latest/). This is a leaky bucket implementation that allows a configurable number of requests per time interval. 

pyrate_limiter supports a variable of backends. The default backend is in-memory, and a single Limiter can be used for both sync and asyncio requests, across multiple threads. Alternative limiters can be used for multiprocess and distributed rate limiting, see [examples](https://github.com/vutran1710/PyrateLimiter/tree/master/examples) for more. 