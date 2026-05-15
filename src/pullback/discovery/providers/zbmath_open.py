"""zbMATH Open discovery provider.

This provider is meant as a no-key fallback when OpenAlex is unavailable or rate-limited.
It queries zbMATH's structured search and extracts arXiv IDs from result links.

zbMATH's `Anywhere` field performs a boolean AND across all terms, so long theorem
statement queries almost always return 404.  We pre-process every query down to its
4–5 key mathematical noun phrases before sending it to the API.
"""

from __future__ import annotations

import asyncio
import re
from typing import Mapping

import httpx

from ...observability import get_logger, trace_span
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from ..arxiv.recovery.title_candidates import extract_title_candidates

log = get_logger("discovery.zbmath_open")

ZBMATH_STRUCTURED_SEARCH_URL = "https://api.zbmath.org/v1/document/_structured_search"

# Common words that carry no signal for zbMATH's AND-search.
# Keeping the list tight: only words that are structurally present in theorem statements
# but never appear as index terms in a math paper.
_ZBMATH_STOPWORDS: frozenset[str] = frozenset("""
a an the of in is are at to for with on and or if by up its from under over
given admits implies every does that which when how one two three also not let
""".split())

_MAX_ZBMATH_TOKENS = 3


def _keywords_for_zbmath(query: str) -> str:
    """Reduce a theorem-statement query to 4-5 key mathematical tokens.

    zbMATH performs AND-search, so long queries with common words produce 404.
    We strip stopwords and take up to _MAX_ZBMATH_TOKENS tokens.
    """
    tokens = re.findall(r"[A-Za-z0-9_\-\^{}]+", query)
    keywords = [t for t in tokens if t.lower() not in _ZBMATH_STOPWORDS and len(t) >= 3]
    return " ".join(keywords[:_MAX_ZBMATH_TOKENS])


class ZbMathOpenDiscoveryClient(PaperDiscoveryClient):
    """zbMATH Open provider using structured search."""

    _MAX_RETRIES = 2
    _BACKOFF_BASE_SECONDS = 1.0

    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._last_title_candidates: list[str] = []

    def title_candidates(self) -> list[str]:
        return list(self._last_title_candidates)

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._BACKOFF_BASE_SECONDS * (2**attempt), 10.0)

    @staticmethod
    def _as_mapping(value: object) -> Mapping[str, object] | None:
        return value if isinstance(value, Mapping) else None

    @classmethod
    def _extract_arxiv_ids_from_hit(cls, hit: Mapping[str, object]) -> list[str]:
        links = hit.get("links")
        if not isinstance(links, list):
            return []
        ids: list[str] = []
        for link in links:
            link_map = cls._as_mapping(link)
            if not link_map:
                continue
            link_type = link_map.get("type")
            if isinstance(link_type, str) and link_type.lower() != "arxiv":
                continue
            for key in ("identifier", "url"):
                value = link_map.get(key)
                if isinstance(value, str):
                    arxiv_id = extract_arxiv_id_from_text(value, allow_bare=True)
                    if arxiv_id:
                        ids.append(arxiv_id)
        return ids

    async def _query_structured(self, client: httpx.AsyncClient, *, query: str, max_results: int) -> dict[str, object]:
        params: dict[str, str | int] = {
            "Anywhere": query,
            "page": 0,
            "results_per_page": max(1, max_results),
        }

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                if self._timeout_seconds > 0:
                    response = await asyncio.wait_for(
                        client.get(ZBMATH_STRUCTURED_SEARCH_URL, params=params),
                        timeout=self._timeout_seconds,
                    )
                else:
                    response = await client.get(ZBMATH_STRUCTURED_SEARCH_URL, params=params)
            except TimeoutError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError(
                        f"zbMATH Open request timed out after {self._timeout_seconds:.1f}s"
                    ) from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue
            except httpx.RequestError as exc:
                if attempt >= self._MAX_RETRIES:
                    raise DiscoveryAccessError("zbMATH Open request failed after retries") from exc
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            # zbMATH uses 404 for "no results".
            if response.status_code == 404:
                return {"result": []}

            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._MAX_RETRIES:
                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(
                    f"zbMATH Open request failed (status {response.status_code}). body={preview}"
                ) from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise DiscoveryAccessError("zbMATH Open returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise DiscoveryAccessError("zbMATH Open returned a non-object payload")
            return payload

        raise DiscoveryAccessError("zbMATH Open exhausted retries without usable response")

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.zbmath_open", query=query, max_results=max_results):
            cleaned = query.strip()
            if not cleaned or max_results <= 0:
                self._last_title_candidates = []
                return []

            # Reduce to keyword tokens so zbMATH's AND-search doesn't hit 404 on
            # long theorem statements.  Fall back to the raw query if extraction
            # yields nothing useful.
            zbmath_query = _keywords_for_zbmath(cleaned) or cleaned
            if zbmath_query != cleaned:
                log.debug("zbmath.query_rewritten original={!r} rewritten={!r}", cleaned, zbmath_query)

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                payload = await self._query_structured(client, query=zbmath_query, max_results=max_results)

            results = payload.get("result")
            if not isinstance(results, list):
                self._last_title_candidates = []
                return []

            self._last_title_candidates = extract_title_candidates(
                [r for r in results if isinstance(r, Mapping)],
                title_key="title",
                max_titles=max_results * 2,
            )

            ids: list[str] = []
            for hit in results:
                hit_map = self._as_mapping(hit)
                if not hit_map:
                    continue
                ids.extend(self._extract_arxiv_ids_from_hit(hit_map))
                if len(ids) >= max_results:
                    break

            ids = dedupe_preserve(ids, max_results=max_results)
            log.info("done count={} ids={}", len(ids), ids)
            return ids
