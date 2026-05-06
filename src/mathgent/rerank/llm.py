"""LLM-based reranker using OpenRouter chat models for batch scoring."""

from __future__ import annotations

import json
import os

import httpx

from ..observability import trace_span
from .base import Reranker


class LLMReranker(Reranker):
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
