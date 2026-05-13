"""Reranker protocol used by forager scoring components."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, snippet: str) -> float: ...

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        """Score multiple snippets for a single query."""
        return [self.score(query, s) for s in snippets]
