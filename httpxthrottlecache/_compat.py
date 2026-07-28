"""Selects the installed HTTP client library: httpx2 (recommended) or httpx"""

# pyright: reportMissingImports=false

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx2 as httpx
    from httpx2._types import ProxyTypes

    HTTPX_IMPL = "httpx2"
else:
    try:
        import httpx2 as httpx
        from httpx2._types import ProxyTypes

        HTTPX_IMPL = "httpx2"
    except ImportError:
        try:
            import httpx
            from httpx._types import ProxyTypes

            HTTPX_IMPL = "httpx"
        except ImportError as e:
            raise ImportError(
                "No HTTP client library installed. Install httpxthrottlecache[httpx2] (recommended) "
                "or httpxthrottlecache[httpx]."
            ) from e

__all__ = ["HTTPX_IMPL", "ProxyTypes", "httpx"]
