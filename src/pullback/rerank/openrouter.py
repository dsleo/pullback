"""OpenRouter reranker using Cohere Rerank v3.5 API."""

from __future__ import annotations

import os

import httpx

from ..observability import trace_span
from .base import Reranker


class OpenRouterReranker(Reranker):
    """Reranker using OpenRouter's rerank API (e.g., Cohere Rerank v3.5)."""

    def __init__(
        self,
        model_name: str = "cohere/rerank-v3.5",
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self._api_key:
            # We don't raise here to allow factory to be created,
            # but score() will fail if called without key.
            pass
        self._url = "https://openrouter.ai/api/v1/rerank"
        self._timeout = timeout

    def score(self, query: str, snippet: str) -> float:
        return self.score_batch(query, [snippet])[0]

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set.")

        with trace_span("reranker.openrouter.score_batch", model=self._model_name, count=len(snippets)):
            response = httpx.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model_name,
                    "query": query,
                    "documents": snippets,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Results are returned as a list of {index: int, relevance_score: float}
            # The order might not be preserved, so we use 'index' to map back.
            scores = [0.0] * len(snippets)
            for result in data["results"]:
                idx = result["index"]
                scores[idx] = float(result["relevance_score"])
            return scores
