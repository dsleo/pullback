import asyncio


def test_title_resolver_uses_abs_title_match(monkeypatch):
    import httpx

    from pullback.discovery.arxiv.title_resolver import resolve_titles_to_arxiv_ids

    # Stub arXiv search HTML results: always return a single candidate id.
    async def _fake_search_get(self, url, **kwargs):  # noqa: ANN001
        if "arxiv.org/search/" in str(url):
            html = '<a href="https://arxiv.org/abs/1511.04069">x</a>'
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))
        # abs page includes citation_title
        html = '<meta name="citation_title" content="Banach fixed point theorem"/>'
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_search_get, raising=True)

    ids = asyncio.run(resolve_titles_to_arxiv_ids(["Banach fixed point theorem"], max_results=5, timeout_seconds=0.01, web_search=None))
    assert ids == ["1511.04069"]


def test_title_resolver_verifies_web_search_results(monkeypatch):
    import httpx

    from pullback.discovery.arxiv.title_resolver import resolve_titles_to_arxiv_ids

    class StubWebSearch:
        async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
            _ = query, max_results
            return ["9999.00001", "1511.04069"]

    async def _fake_get(self, url, **kwargs):  # noqa: ANN001
        target = str(url)
        if target.endswith("/9999.00001"):
            html = '<meta name="citation_title" content="Completely different paper"/>'
        else:
            html = '<meta name="citation_title" content="Banach fixed point theorem"/>'
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get, raising=True)

    ids = asyncio.run(
        resolve_titles_to_arxiv_ids(
            ["Banach fixed point theorem"],
            max_results=5,
            timeout_seconds=0.01,
            web_search=StubWebSearch(),
        )
    )
    assert ids == ["1511.04069"]
