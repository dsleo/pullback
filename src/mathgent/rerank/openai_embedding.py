"""OpenAI embedding-based reranker for semantic similarity."""

from __future__ import annotations

import os

import numpy as np

from ..observability import trace_span
from .base import Reranker


class OpenAIEmbeddingReranker(Reranker):
    """Reranker using OpenAI's text-embedding-3-small for semantic similarity."""

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-small") -> None:
        self._model = model
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai package is required for OpenAIEmbeddingReranker. "
                "Install with: uv add openai"
            ) from exc
        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def score(self, query: str, snippet: str) -> float:
        return self.score_batch(query, [snippet])[0]

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []

        with trace_span("reranker.openai.score_batch", model=self._model, count=len(snippets)):
            # 1. Embed query
            q_res = self._client.embeddings.create(input=[query], model=self._model)
            q_vec = np.array(q_res.data[0].embedding)

            # 2. Embed snippets
            s_res = self._client.embeddings.create(input=snippets, model=self._model)
            s_vecs = [np.array(d.embedding) for d in s_res.data]

            # 3. Cosine similarity
            scores = []
            norm_q = np.linalg.norm(q_vec)
            for s_vec in s_vecs:
                norm_s = np.linalg.norm(s_vec)
                if norm_q == 0 or norm_s == 0:
                    scores.append(0.0)
                else:
                    sim = float(np.dot(q_vec, s_vec) / (norm_q * norm_s))
                    scores.append(max(0.0, sim))
            return scores
