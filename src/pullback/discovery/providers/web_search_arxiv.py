from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Protocol, Sequence

from aiolimiter import AsyncLimiter
from cachetools import TTLCache

from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text
from ..base import PaperDiscoveryClient
from ...observability import get_logger, trace_span

log = get_logger("discovery.web_search_arxiv")


@dataclass(frozen=True)
class WebSearchResult:
    url: str
    title: str | None = None


class WebSearchBackend(Protocol):
    async def search(self, *, query: str, max_results: int) -> Sequence[WebSearchResult]:
        """Return search results (URL + optional title)."""


@dataclass(frozen=True)
class WebSearchArxivConfig:
    site_filter: str = "arxiv.org/abs"

    def build_queries(self, query: str) -> list[str]:
        cleaned = " ".join((query or "").split()).strip()
        if not cleaned:
            return []
        # Two attempts only:
        # 1) strict site filter
        # 2) relaxed query with arXiv hint (post-filtered by URL parsing)
        return [
            f"site:{self.site_filter} {cleaned}",
            f"{cleaned} arxiv",
        ]


class WebSearchArxivDiscoveryClient(PaperDiscoveryClient):
    """Generic web-search-backed arXiv ID discovery provider.

    This is intentionally backend-agnostic so we can swap Brave/SerpApi/etc.
    without touching the discovery pipeline.
    """

    def __init__(
        self,
        *,
        backend: WebSearchBackend,
        config: WebSearchArxivConfig | None = None,
        cache: TTLCache[str, list[str]] | None = None,
        limiter: AsyncLimiter | None = None,
    ) -> None:
        self._backend = backend
        self._config = config or WebSearchArxivConfig()
        self._cache = cache or TTLCache(maxsize=2048, ttl=6 * 60 * 60)
        self._limiter = limiter or AsyncLimiter(1, 0.5)

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "")).strip().lower()

    @classmethod
    def _pick_ids(cls, *, query: str, results: Sequence[WebSearchResult], max_results: int) -> list[str]:
        if max_results <= 0:
            return []
        quoted: str | None = None
        if '"' in query:
            parts = query.split('"')
            if len(parts) >= 3:
                candidate = parts[1].strip()
                if candidate:
                    quoted = cls._norm(candidate)

        ids: list[str] = []
        if quoted:
            for item in results:
                title = item.title
                if isinstance(title, str) and title.strip() and quoted in cls._norm(title):
                    arxiv_id = extract_arxiv_id_from_text(item.url, allow_bare=True)
                    if arxiv_id:
                        ids.append(arxiv_id)
                        break

        if len(ids) < max_results:
            for item in results:
                arxiv_id = extract_arxiv_id_from_text(item.url, allow_bare=True)
                if arxiv_id:
                    ids.append(arxiv_id)
                if len(ids) >= max_results:
                    break

        return dedupe_preserve(ids, max_results=max_results)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.web_search_arxiv", query=query, max_results=max_results):
            if max_results <= 0:
                return []
            qs = self._config.build_queries(query)
            if not qs:
                return []

            for q in qs:
                cached = self._cache.get(q) if self._cache is not None else None  # type: ignore[arg-type]
                if cached:
                    log.info("provider.cache_hit provider=web_search_arxiv query={!r} count={}", q, len(cached))
                    return cached[:max_results]

            last_q = qs[-1]
            for q in qs:
                log.info("provider.attempt provider=web_search_arxiv query={!r}", q)
                async with self._limiter:
                    results = await self._backend.search(query=q, max_results=max_results)
                ids = self._pick_ids(query=query, results=list(results or []), max_results=max_results)
                log.info("provider.attempt_done provider=web_search_arxiv query={!r} ids={}", q, len(ids))
                if self._cache is not None:
                    self._cache[q] = ids
                    log.info("provider.cache_store provider=web_search_arxiv query={!r} count={}", q, len(ids))
                if ids:
                    return ids
                last_q = q

            log.info("done count=0 ids=[]")
            return []
