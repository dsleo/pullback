"""OpenAlex semantic discovery adapter with retry logic and title-to-arXiv resolution."""

from __future__ import annotations

import asyncio
import json
from typing import Mapping
from urllib.parse import urlparse

import httpx

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient, RetryConfig
from ..parsing.arxiv_ids import dedupe_preserve, extract_arxiv_id_from_text
from ..arxiv_title_resolver import ArxivTitleResolver, normalize_title

log = get_logger("discovery.openalex")
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OpenAlexPayload = dict[str, object]
OpenAlexRequestParams = dict[str, str | int]


class OpenAlexDiscoveryClient(PaperDiscoveryClient):
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float = 20.0,
        retry: RetryConfig | None = None,
        title_resolution_enabled: bool = True,
        max_title_resolutions: int = 4,
        mailto: str | None = None,
        title_resolver: ArxivTitleResolver | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._retry = retry or RetryConfig()
        self._title_resolution_enabled = title_resolution_enabled
        self._max_title_resolutions = max(1, max_title_resolutions)
        self._mailto = mailto
        self._title_resolver = title_resolver or ArxivTitleResolver()

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._retry.base_backoff_seconds * (2**attempt), 30.0)

    @staticmethod
    def _retry_after_from_response(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header and header.strip().isdigit():
            return float(header.strip())
        try:
            data = response.json()
        except Exception:
            return None
        retry_after = data.get("retryAfter") if isinstance(data, dict) else None
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
        if isinstance(retry_after, str) and retry_after.strip().isdigit():
            return float(retry_after.strip())
        return None

    async def _query_openalex(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        max_results: int,
    ) -> OpenAlexPayload:
        params: OpenAlexRequestParams = {
            "per-page": max(1, min(max_results * 6, 50)),
            "select": "id,title,ids,primary_location,best_oa_location,locations",
            "search.semantic": query,
        }

        if self._api_key:
            params["api_key"] = self._api_key
        if self._mailto:
            params["mailto"] = self._mailto

        for attempt in range(self._retry.max_retries + 1):
            try:
                response = await client.get(OPENALEX_WORKS_URL, params=params)
            except httpx.RequestError as exc:
                if attempt >= self._retry.max_retries:
                    raise DiscoveryAccessError("OpenAlex request failed after retries.") from exc
                delay = self._backoff_seconds(attempt)
                log.warning("network_retry attempt={} delay_s={:.2f} error={}", attempt + 1, delay, exc)
                await asyncio.sleep(delay)
                continue

            status = response.status_code
            if status == 429 and attempt < self._retry.max_retries:
                retry_after = self._retry_after_from_response(response) or (
                    max(1.1, self._backoff_seconds(attempt))
                )
                log.warning("rate_limited attempt={} delay_s={:.2f}", attempt + 1, retry_after)
                await asyncio.sleep(min(retry_after, 30.0))
                continue

            if status in (500, 502, 503, 504) and attempt < self._retry.max_retries:
                delay = self._backoff_seconds(attempt)
                log.warning("retryable status={} attempt={} delay_s={:.2f}", status, attempt + 1, delay)
                await asyncio.sleep(delay)
                continue

            if status in (401, 403, 429):
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(f"OpenAlex unavailable (status {status}). body={preview}")
            if status == 400:
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(f"OpenAlex bad request (400). body={preview}")

            response.raise_for_status()
            try:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DiscoveryAccessError("OpenAlex returned a non-object JSON payload.")
                return payload
            except json.JSONDecodeError as exc:
                raise DiscoveryAccessError("OpenAlex returned invalid JSON.") from exc

        raise DiscoveryAccessError("OpenAlex exhausted retries without usable response.")

    @staticmethod
    def _as_mapping(value: object) -> Mapping[str, object] | None:
        return value if isinstance(value, Mapping) else None

    @classmethod
    def extract_arxiv_ids_from_openalex(cls, payload: Mapping[str, object], *, max_results: int) -> list[str]:
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        ids: list[str] = []
        for item in results:
            item_map = cls._as_mapping(item)
            if item_map is None:
                continue

            ids_block = cls._as_mapping(item_map.get("ids"))
            if ids_block is not None:
                arxiv_field = ids_block.get("arxiv")
                if isinstance(arxiv_field, str):
                    arxiv_id = extract_arxiv_id_from_text(arxiv_field, allow_bare=True)
                    if arxiv_id:
                        ids.append(arxiv_id)

            candidates: list[str] = []
            for loc_key in ("primary_location", "best_oa_location"):
                loc = cls._as_mapping(item_map.get(loc_key))
                if loc is None:
                    continue
                for field in ("landing_page_url", "pdf_url"):
                    value = loc.get(field)
                    if isinstance(value, str):
                        host = urlparse(value).netloc.lower()
                        if "arxiv.org" in host:
                            candidates.append(value)

            locations = item_map.get("locations")
            if isinstance(locations, list):
                for loc in locations:
                    loc_map = cls._as_mapping(loc)
                    if loc_map is None:
                        continue
                    for field in ("landing_page_url", "pdf_url"):
                        value = loc_map.get(field)
                        if isinstance(value, str):
                            host = urlparse(value).netloc.lower()
                            if "arxiv.org" in host:
                                candidates.append(value)

            for value in candidates:
                arxiv_id = extract_arxiv_id_from_text(value)
                if arxiv_id:
                    ids.append(arxiv_id)

        return dedupe_preserve(ids, max_results=max_results)

    @staticmethod
    def _titles_for_resolution(payload: Mapping[str, object], limit: int) -> list[str]:
        results = payload.get("results") or []
        titles: list[str] = []
        seen: set[str] = set()

        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str):
                continue
            cleaned = " ".join(title.split()).strip()
            if not cleaned:
                continue
            normalized = normalize_title(cleaned)
            if normalized in seen:
                continue
            seen.add(normalized)
            titles.append(cleaned)
            if len(titles) >= limit:
                break

        return titles

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.openalex", query=query, max_results=max_results):
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                payload = await self._query_openalex(client, query=query, max_results=max_results)

            semantic_ids = self.extract_arxiv_ids_from_openalex(payload, max_results=max_results)
            if len(semantic_ids) >= max_results or not self._title_resolution_enabled:
                log.info("semantic.done count={} ids={}", len(semantic_ids), semantic_ids)
                return semantic_ids

            titles = self._titles_for_resolution(
                payload,
                limit=min(self._max_title_resolutions, max_results * 4),
            )
            needed = max_results - len(semantic_ids)
            resolved = await self._title_resolver.resolve_titles(titles, needed=needed)
            merged = dedupe_preserve(semantic_ids + resolved, max_results=max_results)
            log.info(
                "semantic.done count={} direct_ids={} resolved_ids={} merged={}",
                len(merged),
                semantic_ids,
                resolved,
                merged,
            )
            return merged
