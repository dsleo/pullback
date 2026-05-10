"""Direct unit tests for ResultPolicy — the result merge and ranking layer.

These guard against regressions in two critical invariants:
  1. merge_indexed_results: deduplication keeps the higher-scoring match,
     preserves the original discovery index, and upgrades None → real match.
  2. rank_and_trim_results: matched results appear first (score-desc),
     followed by unmatched in discovery order, trimmed to max_results.
"""

from __future__ import annotations

import pytest

from mathgent.models import LemmaMatch, SearchResultEntry
from mathgent.orchestration.result_policy import ResultPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(arxiv_id: str, score: float | None = None) -> SearchResultEntry:
    match = None
    if score is not None:
        match = LemmaMatch(
            arxiv_id=arxiv_id,
            line_number=1,
            header_line=r"\begin{theorem}",
            snippet="Some snippet.",
            score=score,
        )
    return SearchResultEntry(arxiv_id=arxiv_id, match=match)


def _make_aggregate(*entries: SearchResultEntry) -> dict:
    """Build a pre-populated aggregate_results dict in discovery order."""
    agg: dict = {}
    ResultPolicy.merge_indexed_results(
        aggregate_results=agg,
        incoming_results=list(enumerate(entries)),
        next_index=0,
    )
    return agg


# ---------------------------------------------------------------------------
# merge_indexed_results
# ---------------------------------------------------------------------------

class TestMergeIndexedResults:
    def test_new_entry_assigned_sequential_index(self) -> None:
        agg: dict = {}
        next_idx = ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[
                (0, _entry("2401.00001", score=0.8)),
                (1, _entry("2401.00002", score=0.5)),
            ],
            next_index=0,
        )
        assert set(agg.keys()) == {"2401.00001", "2401.00002"}
        assert agg["2401.00001"][0] == 0
        assert agg["2401.00002"][0] == 1
        assert next_idx == 2

    def test_duplicate_keeps_original_index(self) -> None:
        agg: dict = {}
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.5))],
            next_index=0,
        )
        # Second round — same paper with higher score
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.9))],
            next_index=1,
        )
        idx, entry = agg["2401.00001"]
        assert idx == 0, "original discovery index must not change on update"
        assert entry.match is not None
        assert entry.match.score == 0.9

    def test_higher_score_wins_on_duplicate(self) -> None:
        agg: dict = {}
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.9))],
            next_index=0,
        )
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.4))],
            next_index=1,
        )
        _, entry = agg["2401.00001"]
        assert entry.match.score == 0.9, "lower-score duplicate must not overwrite higher-score"

    def test_none_match_does_not_overwrite_real_match(self) -> None:
        agg: dict = {}
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.7))],
            next_index=0,
        )
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=None))],  # no match
            next_index=1,
        )
        _, entry = agg["2401.00001"]
        assert entry.match is not None, "None match must not overwrite an existing real match"
        assert entry.match.score == 0.7

    def test_real_match_upgrades_none_match(self) -> None:
        agg: dict = {}
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=None))],
            next_index=0,
        )
        ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.6))],
            next_index=1,
        )
        _, entry = agg["2401.00001"]
        assert entry.match is not None
        assert entry.match.score == 0.6

    def test_next_index_not_incremented_for_duplicate(self) -> None:
        agg: dict = {}
        n = ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.5))],
            next_index=0,
        )
        assert n == 1
        n2 = ResultPolicy.merge_indexed_results(
            aggregate_results=agg,
            incoming_results=[(0, _entry("2401.00001", score=0.9))],
            next_index=n,
        )
        assert n2 == 1, "index counter must not advance for a duplicate entry"


# ---------------------------------------------------------------------------
# rank_and_trim_results
# ---------------------------------------------------------------------------

class TestRankAndTrimResults:
    def test_matched_before_unmatched(self) -> None:
        indexed = [
            (0, _entry("2401.00001", score=None)),   # discovered first, no match
            (1, _entry("2401.00002", score=0.7)),    # match
        ]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=10)
        assert results[0].arxiv_id == "2401.00002"
        assert results[1].arxiv_id == "2401.00001"

    def test_matched_sorted_by_score_descending(self) -> None:
        indexed = [
            (0, _entry("A", score=0.5)),
            (1, _entry("B", score=0.9)),
            (2, _entry("C", score=0.3)),
        ]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=10)
        scores = [r.match.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_unmatched_in_discovery_order(self) -> None:
        indexed = [
            (2, _entry("C", score=None)),
            (0, _entry("A", score=None)),
            (1, _entry("B", score=None)),
        ]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=10)
        assert [r.arxiv_id for r in results] == ["A", "B", "C"]

    def test_trim_to_max_results(self) -> None:
        indexed = [(i, _entry(f"2401.{i:05d}", score=float(i) / 10)) for i in range(1, 8)]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=3)
        assert len(results) == 3

    def test_trim_prefers_matched_over_unmatched(self) -> None:
        indexed = [
            (0, _entry("match1", score=0.8)),
            (1, _entry("match2", score=0.6)),
            (2, _entry("nomatch1", score=None)),
            (3, _entry("nomatch2", score=None)),
        ]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=3)
        ids = [r.arxiv_id for r in results]
        assert "match1" in ids
        assert "match2" in ids
        # One unmatched makes the cut
        assert sum(1 for r in results if r.match is None) == 1

    def test_empty_input_returns_empty(self) -> None:
        assert ResultPolicy.rank_and_trim_results(indexed_results=[], max_results=5) == []

    def test_all_unmatched_returns_discovery_order(self) -> None:
        indexed = [(2, _entry("C")), (0, _entry("A")), (1, _entry("B"))]
        results = ResultPolicy.rank_and_trim_results(indexed_results=indexed, max_results=10)
        assert [r.arxiv_id for r in results] == ["A", "B", "C"]
