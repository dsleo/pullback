"""OpenAlex semantic discovery adapter with keyword fallback and arXiv ID extraction."""

from __future__ import annotations

import asyncio
from typing import Mapping
import httpx

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text

log = get_logger("discovery.openalex")
OPENALEX_WORKS_URL = "https://api.openalex.org/works"


class OpenAlexDiscoveryClient(PaperDiscoveryClient):
    """OpenAlex provider with semantic search and keyword fallback."""

    _MAX_RETRIES = 3
    _BACKOFF_BASE_SECONDS = 1.0

    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float = 12.0,
        mailto: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._mailto = mailto

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._BACKOFF_BASE_SECONDS * (2**attempt), 30.0)

    @staticmethod
    def _retry_after_from_response(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header and header.strip().isdigit():
            return float(header.strip())
        return None

    async def _query_semantic(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        max_results: int,
    ) -> dict[str, object]:
        target = max(1, max_results)
        per_page = min(max(target * 2, 8), 25)
        params: dict[str, str | int] = {
            "per-page": per_page,
            "select": "id,title,ids,primary_location,best_oa_location,locations",
            "search.semantic": query,
        }
        if self._api_key:
            params["api_key"] = self._api_key
        if self._mailto:
            params["mailto"] = self._mailto

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                if self._timeout_seconds > 0:
                    response = await asyncio.wait_for(
                        client.get(OPENALEX_WORKS_URL, params=params),
                        timeout=self._timeout_seconds,
                    )
                else:
                    response = await client.get(OPENALEX_WORKS_URL, params=params)
            except TimeoutError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError(
                        f"OpenAlex semantic request timed out after {self._timeout_seconds:.1f}s"
                    ) from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue
            except httpx.RequestError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError("OpenAlex semantic request failed after retries") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            if response.status_code == 429 and attempt < self._MAX_RETRIES:
                retry_after = self._retry_after_from_response(response) or self._backoff_seconds(attempt)
                await asyncio.sleep(min(retry_after, 30.0))
                continue

            if response.status_code in (500, 502, 503, 504) and attempt < self._MAX_RETRIES:
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(
                    f"OpenAlex semantic request failed (status {response.status_code}). body={preview}"
                ) from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise DiscoveryAccessError("OpenAlex returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise DiscoveryAccessError("OpenAlex returned a non-object payload")
            return payload

        raise DiscoveryAccessError("OpenAlex exhausted retries without usable response")

    async def _query_keyword(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        max_results: int,
    ) -> dict[str, object]:
        target = max(1, max_results)
        per_page = min(max(target * 2, 8), 25)
        params: dict[str, str | int] = {
            "per-page": per_page,
            "select": "id,title,ids,primary_location,best_oa_location,locations",
            "search": query,
        }
        if self._api_key:
            params["api_key"] = self._api_key
        if self._mailto:
            params["mailto"] = self._mailto

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                if self._timeout_seconds > 0:
                    response = await asyncio.wait_for(
                        client.get(OPENALEX_WORKS_URL, params=params),
                        timeout=self._timeout_seconds,
                    )
                else:
                    response = await client.get(OPENALEX_WORKS_URL, params=params)
            except TimeoutError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError(
                        f"OpenAlex keyword request timed out after {self._timeout_seconds:.1f}s"
                    ) from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue
            except httpx.RequestError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError("OpenAlex keyword request failed after retries") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            if response.status_code == 429 and attempt < self._MAX_RETRIES:
                retry_after = self._retry_after_from_response(response) or self._backoff_seconds(attempt)
                await asyncio.sleep(min(retry_after, 30.0))
                continue

            if response.status_code in (500, 502, 503, 504) and attempt < self._MAX_RETRIES:
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(
                    f"OpenAlex keyword request failed (status {response.status_code}). body={preview}"
                ) from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise DiscoveryAccessError("OpenAlex returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise DiscoveryAccessError("OpenAlex returned a non-object payload")
            return payload

        raise DiscoveryAccessError("OpenAlex exhausted retries without usable response")

    @staticmethod
    def _should_fallback_to_keyword(error: DiscoveryAccessError) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "status 400",  # Bad request (Databricks embedding error)
                "status 403",  # Forbidden (Databricks endpoint down)
                "status 429",  # Rate limit
                "rate limit",
                "insufficient budget",
                "timed out",
                "timeout",
            )
        )

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
            found_for_item = False
            if not isinstance(item, Mapping):
                continue
            ids_mapping = cls._as_mapping(item.get("ids"))
            if ids_mapping and isinstance(ids_mapping.get("arxiv"), str):
                arxiv_id = extract_arxiv_id_from_text(ids_mapping.get("arxiv"))
                if arxiv_id:
                    ids.append(arxiv_id)
                    found_for_item = True
                    continue

            for location_key in ("primary_location", "best_oa_location"):
                location = cls._as_mapping(item.get(location_key))
                if location:
                    candidate = location.get("landing_page_url") or location.get("pdf_url")
                    if isinstance(candidate, str):
                        arxiv_id = extract_arxiv_id_from_text(candidate)
                        if arxiv_id:
                            ids.append(arxiv_id)
                            found_for_item = True
                            break

            locations = item.get("locations")
            if not found_for_item and isinstance(locations, list):
                for location_item in locations:
                    location = cls._as_mapping(location_item)
                    if location:
                        for key in ("landing_page_url", "pdf_url"):
                            candidate = location.get(key)
                            if isinstance(candidate, str):
                                arxiv_id = extract_arxiv_id_from_text(candidate)
                                if arxiv_id:
                                    ids.append(arxiv_id)
                                    break

        return dedupe_preserve(ids, max_results=max_results)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.openalex", query=query, max_results=max_results):
            if not query.strip():
                return []

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                try:
                    payload = await self._query_semantic(client, query=query, max_results=max_results)
                except DiscoveryAccessError as exc:
                    if not self._should_fallback_to_keyword(exc):
                        raise
                    log.warning("semantic_failed_fallback_keyword error={}", exc)
                    payload = await self._query_keyword(client, query=query, max_results=max_results)
            ids = self.extract_arxiv_ids_from_openalex(payload, max_results=max_results)
            log.info("done count={} ids={}", len(ids), ids)
            return ids
