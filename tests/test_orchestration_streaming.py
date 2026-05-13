"""Tests for the streaming foraging and eager discovery behaviour in LibrarianOrchestrator.

These guard against regressions in:
  1. Eager raw-query discovery+foraging starts BEFORE LLM planning completes.
  2. Forager tasks fire immediately per discovery batch (streaming dispatch).
  3. Papers are deduplicated across all discovery batches in a round.
  4. Final results are ranked matched-first by score descending.
  5. discovery_done hook fires once per query attempt.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from pullback.models import LemmaMatch, SearchResponse
from pullback.orchestration import LibrarianOrchestrator


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeReranker:
    def score(self, query: str, snippet: str) -> float:
        return 0.5

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        return [self.score(query, s) for s in snippets]


class _TimestampedForager:
    """Records when each forage call starts (wall time). Used to detect eager dispatch."""
    def __init__(self, delay: float = 0.0) -> None:
        self.start_times: dict[str, float] = {}
        self._delay = delay
        from pullback.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        self.start_times[arxiv_id] = time.perf_counter()
        if self._delay:
            await asyncio.sleep(self._delay)
        return [LemmaMatch(
            arxiv_id=arxiv_id, line_number=1,
            header_line=r"\begin{theorem}", snippet="s", score=0.8,
        )]


class _SlowPlannerDiscovery:
    """Routes by query string: seed query returns immediately, others block.

    Note: the librarian only passes is_raw_query to ChainedDiscoveryClient;
    for plain fake clients the parameter is absent, so we route by query text.
    """
    def __init__(
        self,
        seed_query: str,
        raw_ids: list[str],
        variant_ids: list[str],
        variant_delay: float = 0.15,
    ) -> None:
        self._seed_query = seed_query
        self._raw_ids = raw_ids
        self._variant_ids = variant_ids
        self._variant_delay = variant_delay

    async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
        if query == self._seed_query:
            return self._raw_ids[:max_papers]
        await asyncio.sleep(self._variant_delay)
        return self._variant_ids[:max_papers]


class _ScoredForager:
    """Returns a configurable score per arxiv_id."""
    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        from pullback.rerank import TokenOverlapReranker
        self._reranker = TokenOverlapReranker()

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        score = self._scores.get(arxiv_id, 0.0)
        if score == 0.0:
            return []
        return [LemmaMatch(
            arxiv_id=arxiv_id, line_number=1,
            header_line=r"\begin{theorem}", snippet="s", score=score,
        )]


# ---------------------------------------------------------------------------
# Test 1: eager foraging starts during LLM planning
# ---------------------------------------------------------------------------

def test_eager_foraging_starts_before_planning_completes() -> None:
    """Forager tasks for the raw query must start while the LLM is still planning.

    The eager task discovers for the seed query immediately (no delay).
    Planning (and variant discovery) is artificially slowed.
    We assert that the raw-query forager starts before planning finishes.
    """
    SEED_QUERY = "test:banach"
    PLANNING_DELAY = 0.15
    t0_ref: list[float] = []  # set inside the event loop to avoid asyncio.run() startup skew
    forager = _TimestampedForager(delay=0.0)

    disc = _SlowPlannerDiscovery(
        seed_query=SEED_QUERY,
        raw_ids=["2401.raw1", "2401.raw2"],
        variant_ids=["2401.var1"],
        variant_delay=PLANNING_DELAY,
    )

    # model_name != "test" is required to enable _agentic_query_loop
    orchestrator = LibrarianOrchestrator(
        discovery_client=disc,
        forager=forager,
        model_name="openai:gpt-4o-mock",  # not "test" → enables eager task
        agentic=True,
        max_query_attempts=2,
        max_replan_rounds=1,  # prevent API call for replanning
    )

    # Planning takes PLANNING_DELAY — raw discovery is instant
    async def slow_plan(query: str) -> list[str]:
        await asyncio.sleep(PLANNING_DELAY)
        return [query, query + "-variant"]

    orchestrator._query_attempts = slow_plan  # type: ignore[method-assign]

    # Record t0 inside the event loop to avoid asyncio.run() startup skew (~300ms).
    async def on_search_start(**_) -> None:
        t0_ref.append(time.perf_counter())

    orchestrator.on("search_start", on_search_start)

    asyncio.run(orchestrator.search(SEED_QUERY, max_results=5, strictness=0.0))

    assert t0_ref, "search_start hook never fired"
    assert "2401.raw1" in forager.start_times, (
        f"raw paper must be foraged; got {list(forager.start_times)}"
    )
    t0 = t0_ref[0]
    raw_start = forager.start_times["2401.raw1"]
    elapsed_at_raw_start = raw_start - t0
    assert elapsed_at_raw_start < PLANNING_DELAY * 0.8, (
        f"eager forager started at {elapsed_at_raw_start:.3f}s but planning takes "
        f"{PLANNING_DELAY}s — foraging must begin before planning completes"
    )


# ---------------------------------------------------------------------------
# Test 2: streaming dispatch fires foragers per discovery batch
# ---------------------------------------------------------------------------

def test_streaming_dispatch_fires_foragers_per_discovery_batch() -> None:
    """Papers from variant queries must start foraging as soon as that batch arrives,
    not after all batches complete.

    Two variant queries: one resolves quickly, one slowly.
    The fast variant's forager must start before the slow variant's discovery finishes.
    """
    FAST_DELAY = 0.02
    SLOW_DELAY = 0.15
    forager = _TimestampedForager()

    class StaggeredDiscovery:
        async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
            if "fast" in query:
                await asyncio.sleep(FAST_DELAY)
                return ["2401.fast1"]
            await asyncio.sleep(SLOW_DELAY)
            return ["2401.slow1"]

    orchestrator = LibrarianOrchestrator(
        discovery_client=StaggeredDiscovery(),
        forager=forager,
        agentic=False,  # no eager task; both queries come from _query_attempts
    )

    async def two_attempts(query: str) -> list[str]:
        return [query + "-fast", query + "-slow"]

    orchestrator._query_attempts = two_attempts  # type: ignore[method-assign]

    asyncio.run(orchestrator.search("test:banach", max_results=5, strictness=0.0))

    assert "2401.fast1" in forager.start_times, f"fast paper not foraged: {list(forager.start_times)}"
    assert "2401.slow1" in forager.start_times, f"slow paper not foraged: {list(forager.start_times)}"

    fast_start = forager.start_times["2401.fast1"]
    slow_start = forager.start_times["2401.slow1"]
    gap = slow_start - fast_start
    assert gap > SLOW_DELAY * 0.5, (
        f"fast paper started at {fast_start:.3f}, slow at {slow_start:.3f}, "
        f"gap={gap:.3f}s — fast forager should have a {SLOW_DELAY}s head start"
    )


# ---------------------------------------------------------------------------
# Test 3: deduplication across discovery batches in one round
# ---------------------------------------------------------------------------

def test_deduplication_across_discovery_batches() -> None:
    """Papers discovered by multiple query variants must be foraged exactly once.

    Routing is by query string since is_raw_query isn't passed to plain clients.
    """
    SEED = "test:banach"
    VARIANT = SEED + "-variant"
    SHARED = "2401.shared"
    forager = _TimestampedForager()

    class OverlappingDiscovery:
        async def discover_arxiv_ids(self, query: str, max_papers: int) -> list[str]:
            if query == SEED:
                return [SHARED, "2401.seed_only"]
            return [SHARED, "2401.variant_only"]

    orchestrator = LibrarianOrchestrator(
        discovery_client=OverlappingDiscovery(),
        forager=forager,
        agentic=False,  # use _query_attempts directly, no eager task
    )

    async def two_attempts(query: str) -> list[str]:
        return [SEED, VARIANT]

    orchestrator._query_attempts = two_attempts  # type: ignore[method-assign]

    asyncio.run(orchestrator.search(SEED, max_results=10, strictness=0.0))

    foraged_ids = list(forager.start_times.keys())
    assert foraged_ids.count(SHARED) == 1, f"shared paper foraged {foraged_ids.count(SHARED)} times, expected 1"
    assert SHARED in foraged_ids
    assert "2401.seed_only" in foraged_ids
    assert "2401.variant_only" in foraged_ids


# ---------------------------------------------------------------------------
# Test 4: final results ranked matched-first by score descending
# ---------------------------------------------------------------------------

def test_final_results_ranked_matched_first_by_score() -> None:
    """Results must be: matched papers (score desc) then unmatched, regardless of discovery order."""
    scores = {
        "2401.low":   0.3,
        "2401.high":  0.9,
        "2401.mid":   0.6,
        "2401.miss":  0.0,  # 0.0 → no match returned by _ScoredForager
    }

    class FixedDiscovery:
        async def discover_arxiv_ids(self, query: str, max_papers: int, *, is_raw_query: bool = False) -> list[str]:
            # Return in "wrong" order to test that sorting is applied
            return ["2401.miss", "2401.low", "2401.high", "2401.mid"][:max_papers]

    orchestrator = LibrarianOrchestrator(
        discovery_client=FixedDiscovery(),
        forager=_ScoredForager(scores),
    )

    result = asyncio.run(orchestrator.search("test:banach", max_results=10, strictness=0.0))

    ids = [r.arxiv_id for r in result.results]
    matched = [r for r in result.results if r.match is not None]
    unmatched = [r for r in result.results if r.match is None]

    # All matched before any unmatched
    assert ids.index("2401.high") < ids.index("2401.miss"), "matched must precede unmatched"

    # Matched sorted by score descending
    matched_scores = [r.match.score for r in matched]
    assert matched_scores == sorted(matched_scores, reverse=True), "matched must be score-desc"


# ---------------------------------------------------------------------------
# Test 5: discovery_done hook fires exactly once per query attempt
# ---------------------------------------------------------------------------

def test_discovery_done_fires_once_per_attempt() -> None:
    ATTEMPTS = ["q0", "q1", "q2"]
    discovery_events: list[str] = []

    class SimpleDiscovery:
        async def discover_arxiv_ids(self, query: str, max_papers: int, *, is_raw_query: bool = False) -> list[str]:
            return [f"2401.{hash(query) % 10000:04d}"]

    orchestrator = LibrarianOrchestrator(
        discovery_client=SimpleDiscovery(),
        forager=_TimestampedForager(),
        agentic=True,
    )

    async def fixed_attempts(query: str) -> list[str]:
        return ATTEMPTS

    orchestrator._query_attempts = fixed_attempts  # type: ignore[method-assign]

    async def on_discovery_done(*, query, arxiv_ids, **_) -> None:
        discovery_events.append(query)

    orchestrator.on("discovery_done", on_discovery_done)

    asyncio.run(orchestrator.search("test:banach", max_results=10, strictness=0.0))

    # One event per distinct query (raw query fires from eager task, variants from disc_tasks)
    # The eager task fires discovery_done for q0 (seed_query, which maps to q0 since _query_attempts returns ATTEMPTS)
    # Then variant disc tasks fire for remaining unique queries
    assert len(discovery_events) == len(ATTEMPTS), (
        f"Expected {len(ATTEMPTS)} discovery_done events, got {len(discovery_events)}: {discovery_events}"
    )


# ---------------------------------------------------------------------------
# Test 6: concurrency bounded by semaphore
# ---------------------------------------------------------------------------

def test_forager_concurrency_bounded() -> None:
    """No more than delegate_concurrency foragers run simultaneously."""
    CONCURRENCY = 2
    concurrent_peak = [0]
    current = [0]

    class PeakTrackingForager:
        from pullback.rerank import TokenOverlapReranker
        _reranker = TokenOverlapReranker()

        async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
            current[0] += 1
            concurrent_peak[0] = max(concurrent_peak[0], current[0])
            await asyncio.sleep(0.05)
            current[0] -= 1
            return []

    class ManyDiscovery:
        async def discover_arxiv_ids(self, query: str, max_papers: int, *, is_raw_query: bool = False) -> list[str]:
            return [f"2401.{i:05d}" for i in range(8)]

    orchestrator = LibrarianOrchestrator(
        discovery_client=ManyDiscovery(),
        forager=PeakTrackingForager(),
        delegate_concurrency=CONCURRENCY,
    )
    asyncio.run(orchestrator.search("test:banach", max_results=8, strictness=0.0))

    assert concurrent_peak[0] <= CONCURRENCY, (
        f"Peak concurrency was {concurrent_peak[0]}, limit is {CONCURRENCY}"
    )
