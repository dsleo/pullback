"""Reranker backend implementations (token overlap, BGE cross-encoder, bi-encoder, ColBERT endpoint)."""

from __future__ import annotations

import json
import math
import os
import re

import httpx
import numpy as np

from ..observability import trace_span
from .base import Reranker

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


class OpenRouterReranker:
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
            # OpenRouter/Cohere might have limits on number of documents per request.
            # Cohere typically allows up to 1000. Let's chunk to be safe if needed,
            # but for 731 it should be fine in one go.
            
            # OpenRouter Rerank API follows Cohere's format.
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


class BiEncoderReranker:
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


class HybridReranker:
    """Uses a fast reranker for filtering, then a slow one for high precision."""

    def __init__(
        self,
        fast: Reranker,
        slow: Reranker,
        min_overlap: float = 0.01,
    ) -> None:
        self._fast = fast
        self._slow = slow
        self._min_overlap = min_overlap

    def score(self, query: str, snippet: str) -> float:
        with trace_span("reranker.hybrid.score"):
            fast_score = self._fast.score(query, snippet)
            if fast_score < self._min_overlap:
                return 0.0
            return self._slow.score(query, snippet)

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []
        with trace_span("reranker.hybrid.score_batch", count=len(snippets)):
            fast_scores = self._fast.score_batch(query, snippets)
            results = [0.0] * len(snippets)

            # Filter candidates by min_overlap
            to_slow_indices = [
                i for i, s in enumerate(fast_scores) if s >= self._min_overlap
            ]
            if not to_slow_indices:
                return results

            to_slow_snippets = [snippets[i] for i in to_slow_indices]
            slow_scores = self._slow.score_batch(query, to_slow_snippets)

            for idx, score in zip(to_slow_indices, slow_scores):
                results[idx] = score
            return results


class OpenAIEmbeddingReranker:
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


class FilteredReranker:
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


class LLMReranker:
    """Reranker using an OpenRouter chat model for batch pointwise scoring.

    Sends all snippets in a single prompt and parses a JSON array of scores.
    One API call per query regardless of snippet count — much cheaper than
    per-document rerank APIs. Good default: deepseek/deepseek-chat or
    qwen/qwen-2.5-7b-instruct.
    """

    _PROMPT_TEMPLATE = (
        "You are a relevance judge for mathematical theorem retrieval.\n"
        "Given a query and a list of theorem snippets (LaTeX), return a JSON array "
        "of relevance scores between 0.0 and 1.0, one per snippet, in the same order.\n"
        "Score 1.0 if the snippet directly states or proves what the query describes. "
        "Score 0.0 if completely unrelated. Use intermediate values for partial relevance.\n"
        "Return ONLY a JSON array of numbers, e.g. [0.9, 0.1, 0.7]. No explanation.\n\n"
        "Query: {query}\n\n"
        "Snippets:\n{snippets}"
    )

    def __init__(
        self,
        model_name: str = "deepseek/deepseek-chat",
        api_key: str | None = None,
        timeout: float = 60.0,
        batch_size: int = 20,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self._url = "https://openrouter.ai/api/v1/chat/completions"
        self._timeout = timeout
        self._batch_size = batch_size

    def score(self, query: str, snippet: str) -> float:
        return self.score_batch(query, [snippet])[0]

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        if not snippets:
            return []
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set.")

        results = [0.0] * len(snippets)
        with trace_span("reranker.llm.score_batch", model=self._model_name, count=len(snippets)):
            for batch_start in range(0, len(snippets), self._batch_size):
                batch = snippets[batch_start : batch_start + self._batch_size]
                snippets_text = "\n\n".join(
                    f"[{i + 1}] {s[:500]}" for i, s in enumerate(batch)
                )
                prompt = self._PROMPT_TEMPLATE.format(query=query, snippets=snippets_text)
                response = httpx.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens": 256,
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"].strip()
                # Extract JSON array from response (model may include markdown fences)
                start = content.find("[")
                end = content.rfind("]") + 1
                if start == -1 or end == 0:
                    continue  # Malformed response — leave batch as 0.0
                scores = json.loads(content[start:end])
                for i, score in enumerate(scores[: len(batch)]):
                    results[batch_start + i] = float(score)
        return results


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
