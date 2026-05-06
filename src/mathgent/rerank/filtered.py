"""Filtered reranker using fast filter + slow precision ranking on top-k."""

from __future__ import annotations

from ..observability import trace_span
from .base import Reranker


class FilteredReranker(Reranker):
    """Pre-filters candidates with a fast reranker, then scores top-k with a slow reranker."""

    def __init__(self, fast: Reranker, slow: Reranker, top_k: int = 50) -> None:
        self._fast = fast
        self._slow = slow
        self._top_k = top_k

    def score(self, query: str, snippet: str) -> float:
        return self._slow.score(query, snippet)

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []
        with trace_span("reranker.filtered.score_batch", count=len(snippets), top_k=self._top_k):
            fast_scores = self._fast.score_batch(query, snippets)
            results = [0.0] * len(snippets)

            # Select top-k indices by fast score
            indexed = sorted(enumerate(fast_scores), key=lambda x: x[1], reverse=True)
            top_indices = [i for i, _ in indexed[: self._top_k]]

            if not top_indices:
                return results

            top_snippets = [snippets[i] for i in top_indices]
            slow_scores = self._slow.score_batch(query, top_snippets)

            for idx, score in zip(top_indices, slow_scores):
                results[idx] = score
            return results
