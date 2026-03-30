"""OpenAI web-search discovery adapter with structured output."""

from __future__ import annotations

import asyncio
import json

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text

log = get_logger("discovery.openai_search")


class OpenAISearchDiscoveryClient(PaperDiscoveryClient):
    _MAX_TIMEOUT_SECONDS = 25.0
    _MAX_RETRIES = 2
    _BACKOFF_BASE_SECONDS = 1.0

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str = "gpt-4.1-mini",
        timeout_seconds: float = 12.0,
        max_output_tokens: int = 400,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout_seconds = min(timeout_seconds, self._MAX_TIMEOUT_SECONDS)
        self._max_output_tokens = max(50, max_output_tokens)
        self._client = None

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._BACKOFF_BASE_SECONDS * (2**attempt), 30.0)

    def _openai_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise DiscoveryAccessError("OPENAI_API_KEY is not configured for openai_search discovery.")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise DiscoveryAccessError("openai package is required for openai_search discovery.") from exc
        self._client = AsyncOpenAI(api_key=self._api_key)
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
        with trace_span("discovery.openai_search", query=query, max_results=max_results):
            client = self._openai_client()
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
                    client.responses.create(
                        model=self._model_name,
                        input=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        tools=[{"type": "web_search_preview"}],
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "arxiv_ids",
                                "strict": True,
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "arxiv_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    },
                                    "required": ["arxiv_ids"],
                                },
                            }
                        },
                        max_output_tokens=self._max_output_tokens,
                    ),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError("OpenAI search request timed out after retries.") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue
            except Exception as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError(f"OpenAI search request failed after retries: {exc}") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            output_text = (getattr(response, "output_text", None) or "").strip()
            ids = self._extract_from_structured_output(output_text, max_results=max_results)
            return ids

        raise DiscoveryAccessError("OpenAI search exhausted retries without usable response.")

    @staticmethod
    def _build_prompts(*, query: str, max_results: int) -> tuple[str, str]:
        system_prompt = (
            "Use web search to find arXiv papers relevant to the query. "
            "Return only arXiv IDs. Do not fabricate IDs."
        )
        user_prompt = (
            f"Query: {query}\n"
            f"Return up to {max(1, max_results)} arXiv IDs. "
            "Valid formats: 2401.00001 or math/0301001, without version suffix."
        )
        return system_prompt, user_prompt
