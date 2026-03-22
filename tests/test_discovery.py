import asyncio

from mathgent.discovery import ChainedDiscoveryClient, DiscoveryAccessError, ExaDiscoveryClient, OpenAlexDiscoveryClient
from mathgent.discovery.parsing import (
    extract_arxiv_id_from_text,
    normalize_arxiv_id,
)
from mathgent.discovery.arxiv_title_resolver import ArxivEntry, choose_best_arxiv_title_match


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


def test_extract_arxiv_ids_from_exa_results() -> None:
    results = [
        {"url": "https://arxiv.org/abs/2502.12345v1"},
        {"url": "https://arxiv.org/abs/2502.12345v2"},
        {"text": "See also arXiv:2401.00001v1 for details."},
    ]
    assert ExaDiscoveryClient.extract_arxiv_ids_from_exa_results(results, max_results=5) == [
        "2502.12345",
        "2401.00001",
    ]


def test_choose_best_arxiv_title_match() -> None:
    target = "Banach fixed point theorem in non reflexive spaces"
    candidates = [
        ArxivEntry(arxiv_id="1111.11111", title="Completely unrelated paper"),
        ArxivEntry(arxiv_id="2401.00001", title="Banach fixed point theorem in non-reflexive spaces"),
    ]
    assert choose_best_arxiv_title_match(target, candidates, threshold=0.80) == "2401.00001"


class _StubProvider:
    def __init__(self, ids: list[str], fail: bool = False) -> None:
        self._ids = ids
        self._fail = fail

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        if self._fail:
            raise DiscoveryAccessError("stub failed")
        return self._ids[:max_results]


class _SlowProvider:
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        _ = query, max_results
        await asyncio.sleep(0.05)
        return ["2401.99999"]


def test_chained_discovery_fills_from_backup_provider() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider(["2401.00001"])),
            ("exa", _StubProvider(["2501.00002", "2501.00003"])),
        ]
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 3))
    assert result == ["2401.00001", "2501.00002", "2501.00003"]


def test_chained_discovery_dedupes_across_providers() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider(["2401.00001"])),
            ("exa", _StubProvider(["2401.00001v2", "2501.00003"])),
        ]
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 2))
    assert result == ["2401.00001", "2501.00003"]


def test_chained_discovery_times_out_provider_and_uses_next() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _SlowProvider()),
            ("exa", _StubProvider(["2501.00002"])),
        ],
        provider_timeout_seconds=0.01,
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 1))
    assert result == ["2501.00002"]


def test_chained_discovery_interleaves_provider_results() -> None:
    chain = ChainedDiscoveryClient(
        providers=[
            ("openalex", _StubProvider(["2401.00001", "2401.00002"])),
            ("exa", _StubProvider(["2501.00003", "2501.00004"])),
        ],
    )
    result = asyncio.run(chain.discover_arxiv_ids("banach", 4))
    assert result == ["2401.00001", "2501.00003", "2401.00002", "2501.00004"]


def test_openalex_semantic_title_resolution_used_when_no_direct_arxiv_ids(monkeypatch) -> None:
    client = OpenAlexDiscoveryClient(
        api_key="dummy",
        title_resolution_enabled=True,
        max_title_resolutions=4,
    )

    async def fake_query_openalex(*args, **kwargs):
        _ = args, kwargs
        return {
            "results": [
                {"title": "Banach fixed point theorem in non-reflexive spaces", "ids": {}},
                {"title": "Some unrelated title", "ids": {}},
            ]
        }

    async def fake_resolve_titles(titles: list[str], *, needed: int) -> list[str]:
        assert needed == 2
        assert "Banach fixed point theorem in non-reflexive spaces" in titles
        return ["2401.00001"]

    monkeypatch.setattr(client, "_query_openalex", fake_query_openalex)
    monkeypatch.setattr(client._title_resolver, "resolve_titles", fake_resolve_titles)

    ids = asyncio.run(client.discover_arxiv_ids("banach", 2))
    assert ids == ["2401.00001"]
