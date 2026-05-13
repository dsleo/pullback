"""BGE reranker using cross-encoder for high-precision ranking."""

from __future__ import annotations

import math

from ..observability import trace_span
from .base import Reranker


class BGEReranker(Reranker):
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers is required for BGEReranker. "
                "Install with: uv add sentence-transformers"
            ) from exc

        self._model = CrossEncoder(model_name)

    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.bge.score"):
            raw = float(self._model.predict([(query, snippet)])[0])
            return 1.0 / (1.0 + math.exp(-raw))
