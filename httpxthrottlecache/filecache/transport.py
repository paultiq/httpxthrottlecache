"""An alternative cache using:
- Flat files

"""

import json
import logging
import os
import time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple, Union
from urllib.parse import quote, unquote

import aiofiles
import httpx
from filelock import AsyncFileLock, FileLock

from ..controller import get_rule_for_request

logger = logging.getLogger(__name__)


class AlreadyLockedError(Exception):
    pass


class DualFileStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(
        self,
        path: Path,
        chunk_size: int = 1024 * 1024,
        on_close: Optional[Callable[[], None]] = None,
        async_on_close: Optional[Callable[[], None]] = None,
    ):
        self.path, self.chunk_size = Path(path), chunk_size
        self.on_close, self.async_on_close = on_close, async_on_close

    def __iter__(self):
        with open(self.path, "rb") as f:
            while True:
                b = f.read(self.chunk_size)
                if not b:
                    break
                yield b

    def close(self) -> None:
        if self.on_close:  # pragma: no cover
            self.on_close()

    async def __aiter__(self):
        async with aiofiles.open(self.path, "rb") as f:
            while True:
                b = await f.read(self.chunk_size)
                if not b:
                    break
                yield b

    async def aclose(self) -> None:
        if self.async_on_close:  # pragma: no cover
            await self.async_on_close()


class FileCache:
    def __init__(self, cache_dir: Union[str, Path], locking: bool = True):
        self.cache_dir = Path(cache_dir)
        logger.info("cache_dir=%s", self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.locking = locking

    def _meta_path(self, p: Path) -> Path:
        return p.with_suffix(p.suffix + ".meta")

    def _load_meta(self, p: Path) -> dict[str, str]:
        try:
            return json.loads(self._meta_path(p).read_text())
        except FileNotFoundError:  # pragma: no cover
            return {}

    def to_path(self, host: str, path: str, query: str) -> Path:
        site = host.lower().rstrip(".")
        (self.cache_dir / site).mkdir(parents=True, exist_ok=True)
        name = unquote(path).strip("/").replace("/", "-") or "index"
        if query:
            name += "-" + unquote(query).replace("&", "-").replace("=", "-")
        return self.cache_dir / site / quote(name, safe="._-~")

    def get_if_fresh(
        self, host: str, path: str, query: str, cache_rules: dict[str, dict[str, Union[bool, int]]]
    ) -> tuple[bool, Optional[Path]]:
        cached = get_rule_for_request(request_host=host, target=path, cache_rules=cache_rules)

        if not cached:
            logger.info("No cache policy for %s://%s, not retrieving from cache", host, path)
            return False, None

        p = self.to_path(host=host, path=path, query=query)
        if not p.exists():
            logger.info("Cache file doesn't exist: %s for %s", path, p)
            return False, None

        meta = self._load_meta(p)
        fetched = meta.get("fetched")
        if not fetched:
            return False, p  # pragma: no cover

        if cached is True:
            logger.info("Cache policy allows unlimited cache, returning %s", p)
            return True, p

        age: int = round(time.time() - float(fetched))
        if age < 0:  # pragma: no cover
            raise ValueError(f"Age is less than 0, impossible {age=}, file {path=}")
        logger.info("file is %s seconds old, policy allows caching for up to %s", age, cached)
        return (age <= cached, p)


class _TeeCore:
    def __init__(self, resp: httpx.Response, path: Path, locking: bool, last_modified: str, access_date: str):
        assert path is not None

        self.resp = resp
        self.path = path
        self.tmp = path.with_name(path.name + ".tmp")
        self.lock = FileLock(str(path) + ".lock") if locking else None
        self.fh = None
        if last_modified:
            self.mtime = int(parsedate_to_datetime(last_modified).timestamp())
        else:
            self.mtime = None

        if access_date:
            self.atime = int(parsedate_to_datetime(access_date).timestamp())
        else:
            self.atime = None  # pragma: no cover

    def acquire(self):
        self.lock and self.lock.acquire()  # pyright: ignore[reportUnusedExpression]

    def open_tmp(self):
        self.fh = open(self.tmp, "wb")

    def write(self, chunk: bytes):
        self.fh.write(chunk)  # pyright: ignore[reportOptionalMemberAccess]

    def finalize(self):
        try:
            if self.fh and not self.fh.closed:
                self.fh.flush()
                os.fsync(self.fh.fileno())
                self.fh.close()
                os.replace(self.tmp, self.path)
            try:
                meta_path = self.path.with_suffix(self.path.suffix + ".meta")
                headers = {
                    "content-type": self.resp.headers.get("content-type"),
                    "content-encoding": self.resp.headers.get("content-encoding"),
                }

                meta_path.write_text(json.dumps({"fetched": self.atime, "origin_lm": self.mtime, "headers": headers}))
            except FileNotFoundError:  # pragma: no cover
                pass
        finally:
            if self.lock and getattr(self.lock, "is_locked", False):
                self.lock.release()


class _TeeToDisk(httpx.SyncByteStream):
    def __init__(self, resp: httpx.Response, path: Path, locking: bool, last_modified: str, access_date: str) -> None:
        self.core = _TeeCore(resp, path, locking, last_modified, access_date)

    def __iter__(self) -> Iterator[bytes]:
        self.core.acquire()
        try:
            self.core.open_tmp()
            for chunk in self.core.resp.iter_raw():
                self.core.write(chunk)
                yield chunk
        finally:
            self.core.finalize()

    def close(self) -> None:
        try:
            self.core.resp.close()
        finally:
            self.core.finalize()


class _AsyncTeeToDisk(httpx.AsyncByteStream):
    def __init__(self, resp: httpx.Response, path: Path, locking: bool, last_modified: str, access_date: str):
        self.resp = resp
        self.path = path
        self.tmp = path.with_name(path.name + ".tmp")
        self.lock = AsyncFileLock(str(path) + ".lock") if locking else None
        if last_modified:
            self.mtime = int(parsedate_to_datetime(last_modified).timestamp())
        else:
            self.mtime = None

        if access_date:
            self.atime = int(parsedate_to_datetime(access_date).timestamp())
        else:
            self.atime = None  # pragma: no cover

    async def __aiter__(self):
        if self.lock:
            await self.lock.acquire()
        try:
            async with aiofiles.open(self.tmp, "wb") as f:
                async for chunk in self.resp.aiter_raw():
                    await f.write(chunk)
                    yield chunk
            os.replace(self.tmp, self.path)
            async with aiofiles.open(self.path.with_suffix(self.path.suffix + ".meta"), "w") as m:
                headers = {
                    "content-type": self.resp.headers.get("content-type"),
                    "content-encoding": self.resp.headers.get("content-encoding"),
                }
                await m.write(json.dumps({"fetched": self.atime, "origin_lm": self.mtime, "headers": headers}))
        finally:
            if self.lock:
                await self.lock.release()

    async def aclose(self):
        try:
            await self.resp.aclose()
        finally:
            if self.lock:
                await self.lock.release()


class CachingTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    cache_rules: dict[str, dict[str, Union[bool, int]]]
    streaming_cutoff: int = 8 * 1024 * 1024

    transport: httpx.HTTPTransport
    _cache: FileCache 
    def __init__(
        self,
        cache_dir: Union[str, Path],
        cache_rules: dict[str, dict[str, Union[bool, int]]],
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self._cache = FileCache(cache_dir=cache_dir, locking=True)
        self.transport = transport or httpx.HTTPTransport()
        self.cache_rules = cache_rules

    def _cache_hit_response(self, req: httpx.Request, path: Path, status_code: int = 200):
        """
        TODO: More carefully consider async here. read_text, read_bytes both are blocking.

        Large files are streamed async, so the only blocking events here are for reading small(ish) files
        """
        meta = json.loads(path.with_suffix(path.suffix + ".meta").read_text())
        date = formatdate(timeval=meta["fetched"], usegmt=True)
        last_modified = formatdate(timeval=meta["origin_lm"], usegmt=True)

        ct = meta.get("headers", {}).get("content-type", "application/octet-stream")
        ce = meta.get("headers", {}).get("content-encoding")
        size = os.path.getsize(path)

        headers = [
            ("x-cache", "HIT"),
            ("content-length", str(size)),
            ("Date", date),
            ("Last-Modified", last_modified),
        ]
        if ce:
            headers.append(("content-encoding", ce))
        if ct:
            headers.append(("content-type", ct))

        if size < self.streaming_cutoff:
            # If the file is small, just read it and return it
            return httpx.Response(
                status_code=status_code,
                headers=headers,
                content=path.read_bytes(),
                request=req,
            )
        else:
            # If the file is large, stream it
            return httpx.Response(
                status_code=status_code,
                headers=headers,
                stream=DualFileStream(path),
                request=req,
            )

    def _cache_miss_response(self, req: httpx.Request, net: httpx.Response, path: Path, tee_factory):
        if net.status_code != 200:
            return net

        miss_headers = [
            (k, v)
            for k, v in net.headers.items()
            if k.lower() not in ("transfer-encoding",)  # "content-encoding", "content-length", "transfer-encoding")
        ]
        miss_headers.append(("x-cache", "MISS"))
        return httpx.Response(
            status_code=net.status_code,
            headers=miss_headers,
            stream=tee_factory(
                net, path, self._cache.locking, net.headers.get("Last-Modified"), net.headers.get("Date")
            ),
            request=req,
            extensions={**net.extensions, "decode_content": False},
        )

    def return_if_fresh(self, request: httpx.Request) -> Tuple[Optional[httpx.Response], Optional[Path]]:
        host = request.url.host
        path = request.url.path
        query = request.url.query.decode() if request.url.query else ""

        fresh, path = self._cache.get_if_fresh(host, path, query, self.cache_rules)

        if path:
            if fresh:
                return self._cache_hit_response(request, path), path
            else:
                lm = json.loads(path.with_suffix(path.suffix + ".meta").read_text()).get("origin_lm")
                if lm:
                    request.headers["If-Modified-Since"] = formatdate(timeval=lm, usegmt=True)
                    return None, path
                else:
                    return None, None
        else:
            return None, None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return self.transport.handle_request(request)

        response, path = self.return_if_fresh(request)
        if response:
            return response

        net = self.transport.handle_request(request)
        if net.status_code == 304:
            logger.info("304 for %s", request)
            assert path is not None  # must be true
            return self._cache_hit_response(request, path, status_code=304)

        host = request.url.host
        path = request.url.path
        query = request.url.query.decode() if request.url.query else ""

        path = self._cache.to_path(host, path, query)
        return self._cache_miss_response(request, net, path, _TeeToDisk)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return await self.transport.handle_async_request(request)  # type: ignore[attr-defined]

        response, path = self.return_if_fresh(request)
        if response:
            return response

        net: httpx.Response = await self.transport.handle_async_request(request)
        if net.status_code == 304:
            assert path is not None  # must be true
            logger.info("304 for %s", request)
            return self._cache_hit_response(request, path, status_code=304)

        path = self._cache.to_path(request.url.host, request.url.path, request.url.query.decode())
        return self._cache_miss_response(request, net, path, _AsyncTeeToDisk)
