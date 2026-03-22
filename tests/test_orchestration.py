import asyncio
import time

from mathgent.discovery import PaperMetadata
from mathgent.models import LemmaMatch, SearchResponse
from mathgent.orchestration import LibrarianOrchestrator


class FakeDiscoveryClient:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        assert query
        return ["a", "b", "c"][:max_papers]


class FakeDiscoveryClientMany:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        assert query
        all_ids = ["a", "b", "c", "d", "e", "f"]
        return all_ids[:max_papers]


class FakeForager:
    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        await asyncio.sleep(0.05)
        return LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=10,
            header_line="\\begin{lemma}[Banach]",
            snippet="Let X be complete.",
            score=0.9,
        )


class FailingForager:
    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        if arxiv_id == "b":
            raise RuntimeError("failed download")
        return LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=10,
            header_line="\\begin{lemma}[Banach]",
            snippet="Let X be complete.",
            score=0.9,
        )


class LateMatchForager:
    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        if arxiv_id != "d":
            return None
        return LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=42,
            header_line="\\begin{lemma}[Late but relevant]",
            snippet="Relevant fixed-point lemma.",
            score=0.95,
        )


class PrefixMatchForager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        _ = query, strictness
        self.calls.append(arxiv_id)
        if arxiv_id in {"a", "b", "c"}:
            return LemmaMatch(
                arxiv_id=arxiv_id,
                line_number=5,
                header_line="\\begin{theorem}[Fast]",
                snippet="Fast match.",
                score=0.9,
            )
        return None


class AdaptiveDiscoveryClient:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        _ = max_papers
        if "nonexpansive" in query.lower():
            return ["hit"]
        return ["miss"]


class AdaptiveForager:
    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        _ = query, strictness
        if arxiv_id != "hit":
            return None
        return LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=18,
            header_line="\\begin{lemma}[Adaptive hit]",
            snippet="Adaptive relevant lemma.",
            score=0.92,
        )


class FakeMetadataClient:
    async def fetch_metadata(self, arxiv_ids: list[str]) -> dict[str, PaperMetadata]:
        out: dict[str, PaperMetadata] = {}
        for arxiv_id in arxiv_ids:
            out[arxiv_id] = PaperMetadata(
                title=f"Title for {arxiv_id}",
                authors=["Alice", "Bob"],
            )
        return out


def test_orchestrator_fans_out_with_asyncio_gather() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
    )

    start = time.perf_counter()
    result = asyncio.run(orchestrator.search("banach", max_results=3, strictness=0.2))
    duration = time.perf_counter() - start

    assert isinstance(result, SearchResponse)
    assert [entry.arxiv_id for entry in result.results] == ["a", "b", "c"]
    assert duration < 0.60


def test_orchestrator_does_not_fail_whole_request_if_one_forager_fails() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FailingForager(),
    )

    result = asyncio.run(orchestrator.search("banach", max_results=3, strictness=0.2))
    by_id = {entry.arxiv_id: entry.match for entry in result.results}
    assert by_id["a"] is not None
    assert by_id["b"] is None
    assert by_id["c"] is not None


def test_orchestrator_oversamples_and_surfaces_late_matches() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClientMany(),
        forager=LateMatchForager(),
        candidate_multiplier=2,
        candidate_cap=10,
        delegate_concurrency=2,
    )

    result = asyncio.run(orchestrator.search("banach", max_results=3, strictness=0.2))
    assert len(result.results) == 3
    assert result.results[0].arxiv_id == "d"
    assert result.results[0].match is not None


def test_orchestrator_early_stops_when_enough_matches() -> None:
    forager = PrefixMatchForager()
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClientMany(),
        forager=forager,
        candidate_multiplier=2,
        candidate_cap=10,
        delegate_concurrency=2,
        early_stop_on_matches=True,
    )

    result = asyncio.run(orchestrator.search("banach", max_results=3, strictness=0.2))
    assert len(result.results) == 3
    assert all(item.match is not None for item in result.results)
    assert set(forager.calls).issubset({"a", "b", "c", "d"})
    assert "f" not in forager.calls


def test_orchestrator_query_attempts_default_to_single_query() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="test",
        agentic_query_loop=False,
    )
    attempts = asyncio.run(orchestrator._query_attempts("banach fixed point theorem"))
    assert attempts == ["banach fixed point theorem"]


def test_orchestrator_query_attempts_use_planner_output_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="openai:gpt-5-mini",
        agentic_query_loop=True,
        max_query_attempts=3,
        query_planner_timeout_seconds=1.0,
    )

    class _PlanOut:
        class output:
            queries = ["banach contraction non reflexive", "fixed point nonexpansive mapping"]

    async def fake_run(prompt: str, **kwargs):
        assert "Original query" in prompt
        assert "deps" in kwargs
        assert "usage_limits" in kwargs
        return _PlanOut()

    monkeypatch.setattr(orchestrator.query_planner_agent, "run", fake_run)
    attempts = asyncio.run(orchestrator._query_attempts("banach fixed point theorem"))
    assert attempts == [
        "banach fixed point theorem",
        "banach contraction non reflexive",
        "fixed point nonexpansive mapping",
    ]


def test_orchestrator_replans_when_no_matches(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=AdaptiveDiscoveryClient(),
        forager=AdaptiveForager(),
        model_name="openai:gpt-5-mini",
        agentic_query_loop=True,
        max_query_attempts=2,
        max_replan_rounds=2,
        early_stop_on_matches=True,
    )

    async def fake_attempts(query: str) -> list[str]:
        return [query]

    async def fake_next_seed(*, original_query: str, seen_queries: list[str]) -> str | None:
        _ = original_query
        if seen_queries:
            return "banach fixed point theorem nonexpansive mapping"
        return None

    monkeypatch.setattr(orchestrator, "_query_attempts", fake_attempts)
    monkeypatch.setattr(orchestrator, "_next_replan_seed", fake_next_seed)

    result = asyncio.run(orchestrator.search("banach fixed point theorem", max_results=1, strictness=0.2))
    assert result.results[0].arxiv_id == "hit"
    assert result.results[0].match is not None


def test_orchestrator_skips_replan_when_a_match_exists(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="openai:gpt-5-mini",
        agentic_query_loop=True,
        max_query_attempts=2,
        max_replan_rounds=3,
    )

    async def fail_if_called(*, original_query: str, seen_queries: list[str]) -> str | None:
        _ = original_query, seen_queries
        raise AssertionError("Replan seed should not be requested when matches already exist.")

    monkeypatch.setattr(orchestrator, "_next_replan_seed", fail_if_called)
    result = asyncio.run(orchestrator.search("banach fixed point theorem", max_results=1, strictness=0.2))
    assert result.results
    assert result.results[0].match is not None


def test_orchestrator_attaches_paper_metadata() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        metadata_client=FakeMetadataClient(),
    )

    result = asyncio.run(orchestrator.search("banach", max_results=2, strictness=0.2))
    assert len(result.results) == 2
    assert result.results[0].title == "Title for a"
    assert result.results[0].authors == ["Alice", "Bob"]
