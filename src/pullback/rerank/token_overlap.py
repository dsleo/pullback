"""Token overlap reranker (baseline for fast, lexical matching)."""

from __future__ import annotations

import re

from ..observability import trace_span
from .base import Reranker

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class TokenOverlapReranker(Reranker):
    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.token.score"):
            q_tokens = {t.lower() for t in TOKEN_RE.findall(query)}
            s_tokens = {t.lower() for t in TOKEN_RE.findall(snippet)}
            if not q_tokens or not s_tokens:
                return 0.0
            overlap = len(q_tokens & s_tokens)
            return overlap / len(q_tokens)

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        with trace_span("reranker.token.score_batch", count=len(snippets)):
            q_tokens = {t.lower() for t in TOKEN_RE.findall(query)}
            if not q_tokens:
                return [0.0] * len(snippets)

            results = []
            for snippet in snippets:
                s_tokens = {t.lower() for t in TOKEN_RE.findall(snippet)}
                if not s_tokens:
                    results.append(0.0)
                else:
                    overlap = len(q_tokens & s_tokens)
                    results.append(overlap / len(q_tokens))
            return results
