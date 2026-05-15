import asyncio


def test_web_search_norm_collapses_whitespace() -> None:
    from pullback.discovery.providers.web_search_arxiv import WebSearchArxivDiscoveryClient

    assert WebSearchArxivDiscoveryClient._norm("  Banach   fixed\tpoint\n theorem  ") == "banach fixed point theorem"


def test_web_search_arxiv_extracts_ids_and_caches(monkeypatch):
    from pullback.discovery.providers.web_search_arxiv import WebSearchArxivDiscoveryClient, WebSearchResult

    calls = {"n": 0}

    class StubBackend:
        async def search(self, *, query: str, max_results: int):
            calls["n"] += 1
            # First attempt is strict site filter; later attempts may relax.
            if calls["n"] == 1:
                assert "site:arxiv.org/abs" in query
                return []
            return [
                WebSearchResult(url="https://arxiv.org/abs/2406.15525", title="Unrelated Paper"),
                WebSearchResult(url="https://arxiv.org/abs/math/0403068", title="Banach fixed point theorem"),
                WebSearchResult(url="https://arxiv.org/abs/2406.15525v2", title="Banach fixed point theorem (v2)"),
            ]

    client = WebSearchArxivDiscoveryClient(backend=StubBackend())

    ids1 = asyncio.run(client.discover_arxiv_ids("\"Banach fixed point theorem\"", 10))
    # For quoted queries, prefer the first title-matching result (math/0403068).
    assert ids1[0] == "math/0403068"
    assert calls["n"] == 2

    # Second call should hit cache (no backend call)
    ids2 = asyncio.run(client.discover_arxiv_ids("\"Banach fixed point theorem\"", 10))
    assert ids2[0] == "math/0403068"
    assert calls["n"] == 2
