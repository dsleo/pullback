"""Bi-encoder reranker using fast semantic similarity on CPU."""

from __future__ import annotations

import numpy as np

from ..observability import trace_span
from .base import Reranker


class BiEncoderReranker(Reranker):
    """Fast bi-encoder model for CPU-friendly semantic ranking.

    Uses sentence-transformers bi-encoder (e.g., all-MiniLM-L6-v2).
    Caches query embedding across multiple score() calls for efficiency.

    Much faster than cross-encoders on CPU:
    - Query embedded once: O(1)
    - Docs batched: O(n) vectorized
    - Scoring: O(n) cosine similarity
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers is required for BiEncoderReranker. "
                "Install with: uv add sentence-transformers"
            ) from exc

        self._model = SentenceTransformer(model_name)
        self._query_cache: dict[str, np.ndarray] = {}
        self._snippet_cache: dict[str, np.ndarray] = {}

    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.biencoder.score"):
            # Cache query embedding across multiple calls
            if query not in self._query_cache:
                query_emb = self._model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
                self._query_cache[query] = query_emb

            # Cache snippet embedding
            snippet_key = hash(snippet)
            if snippet_key not in self._snippet_cache:
                snippet_emb = self._model.encode(snippet, convert_to_numpy=True, normalize_embeddings=True)
                self._snippet_cache[snippet_key] = snippet_emb
            else:
                snippet_emb = self._snippet_cache[snippet_key]

            query_emb = self._query_cache[query]

            # Cosine similarity (both normalized)
            score = float(np.dot(query_emb, snippet_emb))
            return max(0.0, score)  # Clamp to [0, 1]
