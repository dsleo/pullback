import asyncio
import json

import httpx


def test_zbmath_exposes_title_candidates(monkeypatch) -> None:
    from pullback.discovery.providers import zbmath_open

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.zbmath.org/v1/document/_structured_search")
        payload = {
            "result": [
                {"title": "First paper title", "links": []},
                {"title": {"value": "Second paper title"}, "links": []},
            ]
        }
        return httpx.Response(
            status_code=200,
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(zbmath_open.httpx, "AsyncClient", patched_async_client)

    client = zbmath_open.ZbMathOpenDiscoveryClient(timeout_seconds=1.0)
    ids = asyncio.run(client.discover_arxiv_ids("banach fixed point theorem", max_results=5))
    assert ids == []
    assert client.title_candidates() == ["First paper title", "Second paper title"]


def test_semantic_scholar_exposes_title_candidates(monkeypatch) -> None:
    from pullback.discovery.providers import semantic_scholar

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.semanticscholar.org/graph/v1/paper/search")
        payload = {
            "data": [
                {"title": "A paper without arxiv id", "externalIds": {}},
                {"title": "A paper with arxiv id", "externalIds": {"ArXiv": "2211.11689"}},
            ]
        }
        return httpx.Response(
            status_code=200,
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(semantic_scholar.httpx, "AsyncClient", patched_async_client)

    client = semantic_scholar.SemanticScholarDiscoveryClient(api_key="dummy", timeout_seconds=1.0)
    ids = asyncio.run(client.discover_arxiv_ids("fixed point theorem", max_results=5))
    assert ids == ["2211.11689"]
    assert client.title_candidates() == ["A paper without arxiv id", "A paper with arxiv id"]

