"""OpenRouter search discovery adapter with structured JSON output."""

from __future__ import annotations

import asyncio
import json

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text

log = get_logger("discovery.openrouter_search")


_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterSearchDiscoveryClient(PaperDiscoveryClient):
    """LLM-as-search discovery client.

    Works with both OpenRouter (default) and native OpenAI by setting
    ``base_url=None`` when constructing the client.
    """

    _MAX_TIMEOUT_SECONDS = 25.0
    _MAX_RETRIES = 2
    _BACKOFF_BASE_SECONDS = 1.0

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str = "openai/gpt-4o-mini",
        timeout_seconds: float = 12.0,
        max_output_tokens: int = 400,
        base_url: str | None = _OPENROUTER_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = min(timeout_seconds, self._MAX_TIMEOUT_SECONDS)
        self._max_output_tokens = max(50, max_output_tokens)
        self._base_url = base_url
        self._client = None

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._BACKOFF_BASE_SECONDS * (2**attempt), 30.0)

    def _openrouter_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            provider = "openrouter_search" if self._base_url else "openai_search"
            key_var = "OPENROUTER_API_KEY" if self._base_url else "OPENAI_API_KEY"
            raise DiscoveryAccessError(f"{key_var} is not configured for {provider} discovery.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise DiscoveryAccessError("openai package is required for LLM search discovery.") from exc
        kwargs: dict = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
            kwargs["default_headers"] = {"HTTP-Referer": "https://mathgent"}
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    @staticmethod
    def _extract_from_structured_output(payload: str, *, max_results: int) -> list[str]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        raw_ids = data.get("arxiv_ids")
        if not isinstance(raw_ids, list):
            return []
        ids: list[str] = []
        for raw in raw_ids:
            if not isinstance(raw, str):
                continue
            arxiv_id = extract_arxiv_id_from_text(raw, allow_bare=True)
            if arxiv_id:
                ids.append(arxiv_id)
        return dedupe_preserve(ids, max_results=max_results)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.openrouter_search", query=query, max_results=max_results):
            client = self._openrouter_client()
            ids = await self._discover_for_query(
                client=client,
                query=query,
                max_results=max_results,
            )
            log.info("done count={} ids={}", len(ids), ids)
            return ids

    async def _discover_for_query(
        self,
        *,
        client: object,
        query: str,
        max_results: int,
    ) -> list[str]:
        system_prompt, user_prompt = self._build_prompts(query=query, max_results=max_results)
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=self._model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=self._max_output_tokens,
                    ),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError("OpenRouter search request timed out after retries.") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue
            except Exception as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError(f"OpenRouter search request failed after retries: {exc}") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            output_text = (response.choices[0].message.content or "").strip()
            ids = self._extract_from_structured_output(output_text, max_results=max_results)
            return ids

        raise DiscoveryAccessError("OpenRouter search exhausted retries without usable response.")

    @staticmethod
    def _build_prompts(*, query: str, max_results: int) -> tuple[str, str]:
        system_prompt = (
            "Use your knowledge to find relevant academic papers on arXiv. "
            "Return only arXiv IDs. Do not fabricate IDs."
        )
        user_prompt = (
            f"Query: {query}\n"
            f"Return up to {max(1, max_results)} arXiv IDs. "
            "Valid formats: 2401.00001 or math/0301001, without version suffix."
        )
        return system_prompt, user_prompt
