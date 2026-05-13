"""ColBERT reranker via HTTP endpoint."""

from __future__ import annotations

import httpx

from ..observability import trace_span
from .base import Reranker


class ModernColBERTReranker(Reranker):
    def __init__(self, endpoint: str = "http://127.0.0.1:8001/rerank", timeout: float = 10.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.colbert.score", endpoint=self._endpoint):
            response = httpx.post(
                self._endpoint,
                json={"query": query, "passages": [snippet]},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                if "scores" in payload and payload["scores"]:
                    return float(payload["scores"][0])
                if "results" in payload and payload["results"]:
                    first = payload["results"][0]
                    if isinstance(first, dict) and "score" in first:
                        return float(first["score"])
            raise RuntimeError("Unsupported ModernColBERT response format.")
