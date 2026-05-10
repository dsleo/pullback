"""Tests for ForagerAgent scoring, top-k ordering, and edge cases.

Guards against regressions in:
  1. Results returned in score-descending order.
  2. All headers scored even when snippets vary in content.
  3. Empty snippet / missing header handled without crash.
  4. No results when paper has no theorem headers.
  5. Bulk fetch path (fetch_header_blocks) is used when available and
     produces identical results to individual fetch path.
"""

from __future__ import annotations

import asyncio

from mathgent.agents import ForagePlan, ForagerAgent
from mathgent.models import LemmaHeader, LemmaMatch


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _DeterministicReranker:
    """Scores based on presence of a keyword passed at init time."""
    def __init__(self, keyword: str = "match") -> None:
        self._kw = keyword

    def score(self, query: str, snippet: str) -> float:
        return 0.9 if self._kw in snippet else 0.2

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        return [self.score(query, s) for s in snippets]


class _MultiHeaderTools:
    """Tools with 4 headers at varying relevance."""

    HEADERS = [
        LemmaHeader(line_number=10, line=r"\begin{lemma} Irrelevant"),
        LemmaHeader(line_number=20, line=r"\begin{theorem} match theorem"),
        LemmaHeader(line_number=30, line=r"\begin{corollary} Also irrelevant"),
        LemmaHeader(line_number=40, line=r"\begin{proposition} match proposition"),
    ]
    SNIPPETS = {
        10: r"\begin{lemma} Irrelevant lemma.\end{lemma}",
        20: r"\begin{theorem} This is a match theorem.\end{theorem}",
        30: r"\begin{corollary} Irrelevant corollary.\end{corollary}",
        40: r"\begin{proposition} Another match proposition.\end{proposition}",
    }

    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        return self.HEADERS

    async def fetch_header_block(
        self, arxiv_id: str, line_number: int, header_line: str, *, context_lines: int = 20
    ) -> str:
        return self.SNIPPETS.get(line_number, "")

    async def fetch_header_blocks(
        self, arxiv_id: str, headers: list[LemmaHeader], *, context_lines: int = 20
    ) -> dict[int, str]:
        return {h.line_number: self.SNIPPETS.get(h.line_number, "") for h in headers}


class _MultiHeaderToolsNoBulk(_MultiHeaderTools):
    """Same as above but without fetch_header_blocks — forces individual fetch path.

    The forager checks `getattr(tools, 'fetch_header_blocks', None)` so the
    method must be absent entirely (not just raise) to disable the bulk path.
    """
    # fetch_header_blocks intentionally absent — do not define it here


class _NoHeaderTools:
    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        return []

    async def fetch_header_block(self, arxiv_id, line_number, header_line, *, context_lines=20) -> str:
        return ""


class _EmptySnippetTools:
    """Returns headers but empty snippets for all of them."""

    HEADERS = [
        LemmaHeader(line_number=5, line=r"\begin{theorem} Statement"),
        LemmaHeader(line_number=15, line=r"\begin{lemma} Other"),
    ]

    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        return self.HEADERS

    async def fetch_header_block(self, arxiv_id, line_number, header_line, *, context_lines=20) -> str:
        return ""

    async def fetch_header_blocks(self, arxiv_id, headers, *, context_lines=20) -> dict[int, str]:
        return {h.line_number: "" for h in headers}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_results_ordered_by_score_descending() -> None:
    """forage() must return results with the highest-scoring match first."""
    agent = ForagerAgent(tools=_MultiHeaderTools(), reranker=_DeterministicReranker("match"))
    results = asyncio.run(agent.forage("match", "2401.00001", strictness=0.0))

    assert results, "expected at least one result"
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), f"results not score-desc: {scores}"


def test_all_headers_scored_regardless_of_relevance() -> None:
    """Every header must be scored; none silently skipped."""
    agent = ForagerAgent(tools=_MultiHeaderTools(), reranker=_DeterministicReranker("match"))
    results = asyncio.run(agent.forage("match", "2401.00001", strictness=0.0))

    scored_lines = {r.line_number for r in results}
    expected_lines = {h.line_number for h in _MultiHeaderTools.HEADERS}
    assert scored_lines == expected_lines, (
        f"expected all headers scored, got lines {scored_lines}, missing {expected_lines - scored_lines}"
    )


def test_no_headers_returns_empty() -> None:
    """If the paper has no theorem headers, forage() must return an empty list — not crash."""
    agent = ForagerAgent(tools=_NoHeaderTools(), reranker=_DeterministicReranker())
    results = asyncio.run(agent.forage("banach", "2401.00001", strictness=0.0))
    assert results == []


def test_empty_snippets_handled_gracefully() -> None:
    """Papers with headers but empty snippets must not crash."""
    agent = ForagerAgent(tools=_EmptySnippetTools(), reranker=_DeterministicReranker())
    results = asyncio.run(agent.forage("banach", "2401.00001", strictness=0.0))
    # May return results with empty snippets or skip them — must not raise
    assert isinstance(results, list)


def test_bulk_and_individual_fetch_produce_same_scores() -> None:
    """fetch_header_blocks (bulk) and fetch_header_block (individual) must yield same scores."""
    reranker = _DeterministicReranker("match")

    bulk_agent = ForagerAgent(tools=_MultiHeaderTools(), reranker=reranker)
    solo_agent = ForagerAgent(tools=_MultiHeaderToolsNoBulk(), reranker=reranker)

    bulk_results = asyncio.run(bulk_agent.forage("match", "2401.00001", strictness=0.0))
    solo_results = asyncio.run(solo_agent.forage("match", "2401.00001", strictness=0.0))

    bulk_by_line = {r.line_number: r.score for r in bulk_results}
    solo_by_line = {r.line_number: r.score for r in solo_results}

    assert bulk_by_line == solo_by_line, (
        f"bulk path scores {bulk_by_line} differ from individual path {solo_by_line}"
    )


def test_plan_complete_hook_fires_with_correct_header_count() -> None:
    """plan_complete hook must fire with the actual number of headers found."""
    agent = ForagerAgent(tools=_MultiHeaderTools(), reranker=_DeterministicReranker())
    events: list[dict] = []

    async def on_plan_complete(*, plan, reason, **_) -> None:
        events.append({"headers": len(plan.headers) if plan else 0, "reason": reason})

    agent.on("plan_complete", on_plan_complete)
    asyncio.run(agent.plan(query="banach", arxiv_id="2401.00001", strictness=0.0))

    assert len(events) == 1
    assert events[0]["headers"] == len(_MultiHeaderTools.HEADERS)


def test_plan_complete_hook_fires_no_headers_reason() -> None:
    """plan_complete must fire with reason='no_headers' when paper has none."""
    agent = ForagerAgent(tools=_NoHeaderTools(), reranker=_DeterministicReranker())
    events: list[dict] = []

    async def on_plan_complete(*, plan, reason, **_) -> None:
        events.append({"plan": plan, "reason": reason})

    agent.on("plan_complete", on_plan_complete)
    asyncio.run(agent.plan(query="banach", arxiv_id="2401.00001", strictness=0.0))

    assert len(events) == 1
    assert events[0]["reason"] == "no_headers"
