import asyncio
import json

import httpx

from pullback.discovery import (
    ChainedDiscoveryClient,
    DiscoveryAccessError,
    OpenRouterSearchDiscoveryClient,
    OpenAlexDiscoveryClient,
    ZbMathOpenDiscoveryClient,
)
from pullback.discovery.arxiv.ids import extract_arxiv_id_from_text, normalize_arxiv_id


def test_extract_arxiv_id_from_text_handles_url_and_versions() -> None:
    assert extract_arxiv_id_from_text("https://arxiv.org/abs/2401.00001v2") == "2401.00001"
    assert extract_arxiv_id_from_text("https://arxiv.org/pdf/math/0301001v1.pdf") == "math/0301001"
    assert extract_arxiv_id_from_text("arXiv:2401.00001v3") == "2401.00001"
    assert extract_arxiv_id_from_text("https://openalex.org/W19904687") is None


def test_normalize_arxiv_id_strips_version_suffix() -> None:
    assert normalize_arxiv_id("2401.00001v9") == "2401.00001"
    assert normalize_arxiv_id("math/0301001v1") == "math/0301001"


def test_extract_arxiv_ids_from_openalex() -> None:
    payload = {
        "results": [
            {
                "ids": {"arxiv": "https://arxiv.org/abs/2401.00001v2"},
                "primary_location": {"landing_page_url": "https://example.org"},
            },
            {
                "ids": {},
                "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/math/0301001v1.pdf"},
            },
            {
                "ids": {"arxiv": "https://arxiv.org/abs/2401.00001v3"},
            },
        ]
    }
    assert OpenAlexDiscoveryClient.extract_arxiv_ids_from_openalex(payload, max_results=5) == [
        "2401.00001",
        "math/0301001",
    ]



def test_extract_arxiv_ids_from_openrouter_structured_output() -> None:
    payload = json.dumps(
        {
            "arxiv_ids": [
                "2509.13121",
                "https://arxiv.org/abs/math/9302208v1",
                "arXiv:2207.03057v2",
            ]
        }
    )
    assert OpenRouterSearchDiscoveryClient._extract_from_structured_output(payload, max_results=5) == [
        "2509.13121",
        "math/9302208",
        "2207.03057",
    ]


class _StubProvider:
    def __init__(self, ids: list[str], fail: bool = False) -> None:
        self._ids = ids
        self._fail = fail
        self.calls = 0

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query
        self.calls += 1
        if self._fail:
            raise DiscoveryAccessError("stub failed")
        return self._ids[:max_results]


class _SlowProvider:
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query, max_results
        await asyncio.sleep(0.05)
        return ["2401.99999"]


class _GenericFailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query, max_results
        self.calls += 1
        raise DiscoveryAccessError("temporary upstream failure")


def test_chained_discovery_fills_from_backup_provider() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider(["2401.00001"])),
            ("openrouter_search", _StubProvider(["2501.00002", "2501.00003"])),
        ]
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 3))
    assert result == ["2401.00001", "2501.00002", "2501.00003"]


def test_chained_discovery_dedupes_across_providers() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider(["2401.00001"])),
            ("openrouter_search", _StubProvider(["2401.00001v2", "2501.00003"])),
        ]
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 2))
    assert result == ["2401.00001", "2501.00003"]


def test_chained_discovery_times_out_provider_and_uses_next() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _SlowProvider()),
            ("openrouter_search", _StubProvider(["2501.00002"])),
        ],
        provider_timeout_seconds=0.01,
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 1))
    assert result == ["2501.00002"]


def test_chained_discovery_keeps_fallback_on_provider_failures() -> None:
    openalex = _GenericFailingProvider()
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", openalex),
            ("openrouter_search", _StubProvider(["2501.00002"])),
        ],
    )

    async def _run() -> None:
        first = await chain.discover_arxiv_ids("query one", 1)
        assert first == ["2501.00002"]
        second = await chain.discover_arxiv_ids("query two", 1)
        assert second == ["2501.00002"]
        assert openalex.calls == 2

    asyncio.run(_run())


def test_chained_discovery_raises_on_total_failure() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider([], fail=True)),
            ("openrouter_search", _StubProvider([], fail=True)),
        ]
    )
    try:
        asyncio.run(chain.discover_arxiv_ids("banach", 2))
    except DiscoveryAccessError as exc:
        assert "No discovery provider" in str(exc)
    else:
        raise AssertionError("Expected DiscoveryAccessError")


def test_openalex_query_uses_bounded_per_page() -> None:
    captured_per_page: int | None = None

    async def _run() -> None:
        nonlocal captured_per_page

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_per_page
            per_page_raw = request.url.params.get("per-page")
            captured_per_page = int(per_page_raw) if per_page_raw is not None else None
            return httpx.Response(
                status_code=200,
                content=json.dumps({"results": []}),
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(handler)
        client = OpenAlexDiscoveryClient(api_key="dummy")
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as http_client:
            payload = await client._query_semantic(
                http_client,
                query="banach fixed point",
                max_results=50,
            )
        assert payload == {"results": []}

    asyncio.run(_run())
    assert captured_per_page == 25


def test_openalex_fallback_detector_triggers_on_rate_limit() -> None:
    client = OpenAlexDiscoveryClient(api_key="dummy")
    error = DiscoveryAccessError(
        "OpenAlex semantic request failed (status 429). body={\"error\":\"Rate limit exceeded\"}"
    )
    assert client._should_fallback_to_keyword(error) is True


def test_zbmath_404_is_treated_as_empty() -> None:
    async def _run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url).startswith("https://api.zbmath.org/v1/document/_structured_search")
            return httpx.Response(status_code=404, content=b"{\"result\":null,\"status\":{}}")

        transport = httpx.MockTransport(handler)
        client = ZbMathOpenDiscoveryClient(timeout_seconds=2.0)
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as http_client:
            payload = await client._query_structured(http_client, query="banach", max_results=5)
        assert payload == {"result": []}

    asyncio.run(_run())


def test_zbmath_discover_arxiv_ids_dedupes() -> None:
    from pullback.discovery.arxiv.ids import dedupe_preserve

    client = ZbMathOpenDiscoveryClient(timeout_seconds=2.0)
    hit = {
        "links": [
            {"identifier": "2210.03406", "type": "arxiv", "url": "https://arxiv.org/abs/2210.03406"},
            {"identifier": "2210.03406", "type": "arxiv", "url": "https://arxiv.org/abs/2210.03406"},
        ]
    }
    ids = client._extract_arxiv_ids_from_hit(hit)
    assert ids.count("2210.03406") >= 2
    assert dedupe_preserve(ids, max_results=10) == ["2210.03406"]
