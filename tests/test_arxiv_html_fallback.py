import asyncio

import pytest


def test_html_href_parsing_strips_version_suffix() -> None:
    from pullback.discovery.providers.arxiv_search_html import _extract_ids_from_hrefs

    assert _extract_ids_from_hrefs(["https://arxiv.org/abs/2406.15525v3"]) == ["2406.15525"]


def test_parse_arxiv_search_html_extracts_ids():
    from pullback.discovery.providers.arxiv_search_html import parse_arxiv_search_html

    html = """
    <html>
      <body>
        <a href="https://arxiv.org/abs/2406.15525">paper</a>
        <a href="/abs/math/0403068">old</a>
        <a href="https://arxiv.org/abs/2406.15525v2">dup</a>
      </body>
    </html>
    """
    ids = parse_arxiv_search_html(html, max_results=10)
    assert ids == ["2406.15525", "math/0403068"]


def test_query_id_cache_ttl_expires():
    import time

    from cachetools import TTLCache

    cache: TTLCache[str, list[str]] = TTLCache(maxsize=10, ttl=0.01)
    cache["banach fixed point theorem"] = ["1234.5678"]
    assert cache.get("banach fixed point theorem") == ["1234.5678"]
    time.sleep(0.02)
    assert cache.get("banach fixed point theorem") is None


def test_arxiv_api_falls_back_to_html_on_429(monkeypatch):
    from pullback.discovery.providers.arxiv_api import ArxivAPIDiscoveryClient

    class StubHtml:
        async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
            return ["1511.04069"]
    class StubWeb:
        async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
            return ["9999.0000"]

    # Speed up retry loops in the arXiv API provider.
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    # Force the Atom API to return 429.
    import httpx

    async def _fake_get(self, url, **kwargs):  # noqa: ANN001
        return httpx.Response(429, text="Rate exceeded.", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get, raising=True)

    client = ArxivAPIDiscoveryClient(timeout_seconds=0.01, html_fallback=StubHtml(), web_fallback=StubWeb())

    ids = asyncio.run(client.discover_arxiv_ids("Banach fixed point theorem", 5))
    assert ids == ["1511.04069"]


def test_arxiv_api_falls_back_to_web_search_when_html_empty(monkeypatch):
    from pullback.discovery.providers.arxiv_api import ArxivAPIDiscoveryClient

    class StubHtml:
        async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
            return []

    class StubWeb:
        async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
            return ["2406.15525"]

    import httpx

    async def _fake_get(self, url, **kwargs):  # noqa: ANN001
        return httpx.Response(429, text="Rate exceeded.", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get, raising=True)

    client = ArxivAPIDiscoveryClient(timeout_seconds=0.01, html_fallback=StubHtml(), web_fallback=StubWeb())
    ids = asyncio.run(client.discover_arxiv_ids("Banach fixed point theorem", 5))
    assert ids == ["2406.15525"]


def test_arxiv_search_html_uses_cache(monkeypatch):
    import httpx

    from pullback.discovery.providers.arxiv_search_html import ArxivSearchHtmlDiscoveryClient
    from cachetools import TTLCache

    cache: TTLCache[str, list[str]] = TTLCache(maxsize=10, ttl=60)
    cache["Banach fixed point theorem"] = ["2406.15525"]

    async def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("network should not be called when cache hits")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom, raising=True)

    client = ArxivSearchHtmlDiscoveryClient(timeout_seconds=0.01, cache=cache, rate_limiter=None)
    ids = asyncio.run(client.discover_arxiv_ids("Banach fixed point theorem", 10))
    assert ids == ["2406.15525"]
