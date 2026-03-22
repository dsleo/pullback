"""Reranker backend implementations (token overlap, BGE cross-encoder, ColBERT endpoint)."""

from __future__ import annotations

import math
import re

import httpx

from ..observability import trace_span

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class TokenOverlapReranker:
    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.token.score"):
            q_tokens = {t.lower() for t in TOKEN_RE.findall(query)}
            s_tokens = {t.lower() for t in TOKEN_RE.findall(snippet)}
            if not q_tokens or not s_tokens:
                return 0.0
            overlap = len(q_tokens & s_tokens)
            return overlap / len(q_tokens)


class BGEReranker:
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


class ModernColBERTReranker:
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
