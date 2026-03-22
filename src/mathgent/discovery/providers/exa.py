"""Exa discovery adapter using exa-py with retries and arXiv ID extraction from results."""

from __future__ import annotations

import asyncio
from typing import Mapping, Protocol, Sequence, cast

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient, RetryConfig
from ..parsing.arxiv_ids import extract_arxiv_ids_from_text_fields

log = get_logger("discovery.exa")


class _ExaSearchResult(Protocol):
    results: list[object]


class _AsyncExaClient(Protocol):
    async def search(
        self,
        query: str,
        *,
        type: str,
        num_results: int,
        category: str,
        include_domains: list[str],
    ) -> _ExaSearchResult: ...


class ExaDiscoveryClient(PaperDiscoveryClient):
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float = 20.0,
        retry: RetryConfig | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._retry = retry or RetryConfig()
        self._exa_client: _AsyncExaClient | None = None

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._retry.base_backoff_seconds * (2**attempt), 30.0)

    def _client(self) -> _AsyncExaClient:
        if self._exa_client is not None:
            return self._exa_client

        if not self._api_key:
            raise DiscoveryAccessError("EXA_API_KEY is not configured.")

        try:
            from exa_py import AsyncExa  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise DiscoveryAccessError("exa-py is required for Exa discovery. Install with: uv add exa-py") from exc

        self._exa_client = cast(_AsyncExaClient, AsyncExa(api_key=self._api_key))
        return self._exa_client

    @staticmethod
    def _result_to_dict(item: object) -> dict[str, str]:
        if isinstance(item, Mapping):
            out_from_mapping: dict[str, str] = {}
            for key in ("url", "id", "title", "text"):
                value = item.get(key)
                if isinstance(value, str):
                    out_from_mapping[key] = value
            return out_from_mapping
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                out_from_dump: dict[str, str] = {}
                for key in ("url", "id", "title", "text"):
                    value = dumped.get(key)
                    if isinstance(value, str):
                        out_from_dump[key] = value
                return out_from_dump
        out: dict[str, str] = {}
        for key in ("url", "id", "title", "text"):
            value = getattr(item, key, None)
            if isinstance(value, str):
                out[key] = value
        return out

    @classmethod
    def extract_arxiv_ids_from_exa_results(
        cls,
        results: Sequence[Mapping[str, object]],
        *,
        max_results: int,
    ) -> list[str]:
        _ = cls
        return extract_arxiv_ids_from_text_fields(
            results,
            max_results=max_results,
            field_names=("url", "id", "title", "text"),
        )

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.exa", query=query, max_results=max_results):
            exa = self._client()
            request_size = max(1, min(max_results * 3, 50))

            for attempt in range(self._retry.max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        exa.search(
                            query,
                            type="auto",
                            num_results=request_size,
                            category="research paper",
                            include_domains=["arxiv.org"],
                        ),
                        timeout=self._timeout_seconds,
                    )
                except TimeoutError as exc:
                    if attempt >= self._retry.max_retries:
                        raise DiscoveryAccessError("Exa request timed out after retries.") from exc
                    delay = self._backoff_seconds(attempt)
                    log.warning("timeout_retry attempt={} delay_s={:.2f}", attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                except Exception as exc:
                    if attempt >= self._retry.max_retries:
                        raise DiscoveryAccessError(f"Exa request failed after retries: {exc}") from exc
                    delay = self._backoff_seconds(attempt)
                    log.warning("request_retry attempt={} delay_s={:.2f} error={}", attempt + 1, delay, exc)
                    await asyncio.sleep(delay)
                    continue

                raw_results: list[dict[str, str]] = []
                items = getattr(response, "results", None)
                if isinstance(items, list):
                    raw_results = [self._result_to_dict(item) for item in items]

                ids = self.extract_arxiv_ids_from_exa_results(raw_results, max_results=max_results)
                log.info("done count={} ids={}", len(ids), ids)
                return ids

            raise DiscoveryAccessError("Exa exhausted retries without usable response.")
