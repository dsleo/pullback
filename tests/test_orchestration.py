import asyncio
import time

from mathgent.discovery import PaperMetadata
from mathgent.models import LemmaMatch, SearchResponse
from mathgent.orchestration import LibrarianOrchestrator


class FakeDiscoveryClient:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        assert query
        return ["2401.00001", "2401.00002", "2401.00003"][:max_papers]


class FakeDiscoveryClientMany:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        assert query
        all_ids = ["2401.00001", "2401.00002", "2401.00003", "2401.00004", "2401.00005", "2401.00006"]
        return all_ids[:max_papers]


class FakeForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        await asyncio.sleep(0.05)
        return [LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=10,
            header_line="\\begin{lemma}[Banach]",
            snippet="Let X be complete.",
            score=0.9,
        )]


class FailingForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        if arxiv_id == "2401.00002":
            raise RuntimeError("failed download")
        return [LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=10,
            header_line="\\begin{lemma}[Banach]",
            snippet="Let X be complete.",
            score=0.9,
        )]


class LateMatchForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        if arxiv_id != "2401.00004":
            return []
        return [LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=42,
            header_line="\\begin{lemma}[Late but relevant]",
            snippet="Relevant fixed-point lemma.",
            score=0.95,
        )]


class PrefixMatchForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()
        self.calls: list[str] = []

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        _ = query, strictness
        self.calls.append(arxiv_id)
        if arxiv_id in {"2401.00001", "2401.00002", "2401.00003"}:
            return [LemmaMatch(
                arxiv_id=arxiv_id,
                line_number=5,
                header_line="\\begin{theorem}[Fast]",
                snippet="Fast match.",
                score=0.9,
            )]
        return []


class CountingForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()
        self.calls: list[str] = []

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        _ = query, strictness
        self.calls.append(arxiv_id)
        return [LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=5,
            header_line="\\begin{theorem}[Fast]",
            snippet="Fast match.",
            score=0.9,
        )]


class AdaptiveDiscoveryClient:
    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        _ = max_papers
        if "nonexpansive" in query.lower():
            return ["2501.00001"]
        return ["2501.00002"]


class AdaptiveForager:
    def __init__(self) -> None:
        from mathgent.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        _ = query, strictness
        if arxiv_id != "2501.00001":
            return []
        return [LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=18,
            header_line="\\begin{lemma}[Adaptive hit]",
            snippet="Adaptive relevant lemma.",
            score=0.92,
        )]


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
    result = asyncio.run(orchestrator.search("test:banach", max_results=3, strictness=0.2))
    duration = time.perf_counter() - start

    assert isinstance(result, SearchResponse)
    assert [entry.arxiv_id for entry in result.results] == ["2401.00001", "2401.00002", "2401.00003"]
    assert duration < 0.60


def test_orchestrator_does_not_fail_whole_request_if_one_forager_fails() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FailingForager(),
    )

    result = asyncio.run(orchestrator.search("test:banach", max_results=3, strictness=0.2))
    by_id = {entry.arxiv_id: entry.match for entry in result.results}
    assert by_id["2401.00001"] is not None
    assert by_id["2401.00002"] is None
    assert by_id["2401.00003"] is not None


def test_orchestrator_limits_candidates_to_max_results() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClientMany(),
        forager=LateMatchForager(),
        delegate_concurrency=2,
    )

    result = asyncio.run(orchestrator.search("test:banach", max_results=3, strictness=0.2))
    assert len(result.results) == 3
    assert [entry.arxiv_id for entry in result.results] == ["2401.00001", "2401.00002", "2401.00003"]
    assert all(entry.match is None for entry in result.results)


def test_orchestrator_dedupes_candidates_across_attempts() -> None:
    forager = CountingForager()
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=forager,
        model_name="test",
        agentic=False,
        max_query_attempts=2,
    )

    async def fake_attempts(query: str) -> list[str]:
        _ = query
        return ["q1", "q2"]

    async def fake_discover(query: str, max_results: int, *, is_raw_query: bool = False) -> list[str]:
        _ = max_results
        if query == "q1":
            return ["2401.00001", "2401.00002", "2401.00003"]
        return ["2401.00002", "2401.00003", "2401.00004"]

    orchestrator._query_attempts = fake_attempts  # type: ignore[method-assign]
    orchestrator._discover_arxiv_ids = fake_discover  # type: ignore[method-assign]

    result = asyncio.run(orchestrator.search("test:banach", max_results=4, strictness=0.2))
    assert [entry.arxiv_id for entry in result.results] == ["2401.00001", "2401.00002", "2401.00003", "2401.00004"]
    assert forager.calls == ["2401.00001", "2401.00002", "2401.00003", "2401.00004"]


def test_orchestrator_query_attempts_default_keep_base_query_first() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="test",
        agentic=False,
    )
    attempts = asyncio.run(orchestrator._query_attempts("banach fixed point theorem"))
    assert attempts
    assert attempts[0] == "banach fixed point theorem"


def test_orchestrator_query_attempts_use_planner_output_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="openai:gpt-5-mini",
        agentic=True,
        max_query_attempts=3,
        timeout_seconds=1.0,
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
        agentic=True,
        max_query_attempts=2,
        max_replan_rounds=2,
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

    result = asyncio.run(orchestrator.search("test:banach fixed point theorem", max_results=1, strictness=0.2))
    assert result.results[0].arxiv_id == "2501.00001"
    assert result.results[0].match is not None


def test_orchestrator_skips_replan_when_a_match_exists(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        model_name="openai:gpt-5-mini",
        agentic=True,
        max_query_attempts=2,
        max_replan_rounds=3,
    )

    async def fail_if_called(*, original_query: str, seen_queries: list[str]) -> str | None:
        _ = original_query, seen_queries
        raise AssertionError("Replan seed should not be requested when matches already exist.")

    monkeypatch.setattr(orchestrator, "_next_replan_seed", fail_if_called)
    result = asyncio.run(orchestrator.search("test:banach fixed point theorem", max_results=1, strictness=0.2))
    assert result.results
    assert result.results[0].match is not None


def test_orchestrator_attaches_paper_metadata() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
        metadata_fetcher=FakeMetadataClient(),
    )

    result = asyncio.run(orchestrator.search("test:banach", max_results=2, strictness=0.2))
    assert len(result.results) == 2
    assert result.results[0].title == "Title for 2401.00001"
    assert result.results[0].authors == ["Alice", "Bob"]


def test_orchestrator_emits_worker_hooks() -> None:
    orchestrator = LibrarianOrchestrator(
        discovery_client=FakeDiscoveryClient(),
        forager=FakeForager(),
    )
    events: list[str] = []

    def on_worker_start(*, state, **_kwargs) -> None:
        events.append(f"start:{state.arxiv_id}")

    def on_worker_done(*, state, result, **_kwargs) -> None:
        status = "hit" if result.match is not None else "miss"
        events.append(f"done:{state.arxiv_id}:{status}")

    orchestrator.on("worker_start", on_worker_start)
    orchestrator.on("worker_done", on_worker_done)

    result = asyncio.run(orchestrator.search("test:banach", max_results=2, strictness=0.2))
    assert len(result.results) == 2
    assert events
    assert events[0].startswith("start:")
    assert any(evt.startswith("done:2401.00001") for evt in events)
