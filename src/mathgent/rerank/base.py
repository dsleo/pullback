"""Reranker protocol used by forager scoring components."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, snippet: str) -> float: ...
