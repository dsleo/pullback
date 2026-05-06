"""Hybrid reranker using fast filter + slow precision ranking."""

from __future__ import annotations

from ..observability import trace_span
from .base import Reranker


class HybridReranker(Reranker):
    """Uses a fast reranker for filtering, then a slow one for high precision."""

    def __init__(
        self,
        fast: Reranker,
        slow: Reranker,
        min_overlap: float = 0.01,
    ) -> None:
        self._fast = fast
        self._slow = slow
        self._min_overlap = min_overlap

    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.hybrid.score"):
            fast_score = self._fast.score(query, snippet)
            if fast_score < self._min_overlap:
                return 0.0
            return self._slow.score(query, snippet)

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []
        with trace_span("reranker.hybrid.score_batch", count=len(snippets)):
            fast_scores = self._fast.score_batch(query, snippets)
            results = [0.0] * len(snippets)

            # Filter candidates by min_overlap
            to_slow_indices = [
                i for i, s in enumerate(fast_scores) if s >= self._min_overlap
            ]
            if not to_slow_indices:
                return results

            to_slow_snippets = [snippets[i] for i in to_slow_indices]
            slow_scores = self._slow.score_batch(query, to_slow_snippets)

            for idx, score in zip(to_slow_indices, slow_scores):
                results[idx] = score
            return results
