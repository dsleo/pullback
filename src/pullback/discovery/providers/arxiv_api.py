"""arXiv export API discovery provider."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import httpx
from aiolimiter import AsyncLimiter
from cachetools import TTLCache

from ...observability import get_logger, trace_span
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from .arxiv_search_html import ArxivSearchHtmlDiscoveryClient

log = get_logger("discovery.arxiv_api")
_ARXIV_API_URLS = (
    "https://export.arxiv.org/api/query",
    "https://arxiv.org/api/query",
)
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_CACHE = TTLCache(maxsize=4096, ttl=6 * 60 * 60)


def _default_limiter() -> AsyncLimiter:
    # Create a limiter per event loop to avoid cross-loop reuse issues.
    return AsyncLimiter(1, 0.5)  # ~2 requests/second per process


class ArxivAPIDiscoveryClient(PaperDiscoveryClient):
    """Lightweight arXiv API provider (no API key required)."""

    _SORT_MAP = {
        "relevance": "relevance",
        "date": "submittedDate",
        "updated": "lastUpdatedDate",
    }

    def __init__(
        self,
        *,
        timeout_seconds: float = 12.0,
        sort: str = "relevance",
        user_agent: str = "pullback/0.1",
        cache: TTLCache[str, list[str]] | None = None,
        rate_limiter: AsyncLimiter | None = None,
        html_fallback: ArxivSearchHtmlDiscoveryClient | None = None,
        web_fallback: PaperDiscoveryClient | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._sort = sort
        self._user_agent = user_agent
        self._cache = _DEFAULT_CACHE if cache is None else cache
        self._rate_limiter = _default_limiter() if rate_limiter is None else rate_limiter
        self._html_fallback = html_fallback
        self._web_fallback = web_fallback

    async def _fallback_chain(self, query: str, max_results: int, *, reason: str) -> list[str]:
        """Fallback order: HTML search, then optional web-search backend."""
        cleaned = query.strip()
        if not cleaned:
            return []

        # 1) HTML fallback (arXiv search page)
        if self._html_fallback is None:
            self._html_fallback = ArxivSearchHtmlDiscoveryClient(
                timeout_seconds=self._timeout_seconds,
                user_agent=self._user_agent,
                cache=self._cache,
                rate_limiter=self._rate_limiter,
            )
        log.warning(
            "provider.fallback_start provider=arxiv_api fallback=arxiv_search_html reason={} query={!r}",
            reason,
            cleaned,
        )
        try:
            ids = await self._html_fallback.discover_arxiv_ids(cleaned, max_results)
        except Exception as fb_exc:  # pragma: no cover
            log.warning(
                "provider.fallback_failed provider=arxiv_api fallback=arxiv_search_html query={!r} error_type={} error_repr={}",
                cleaned,
                type(fb_exc).__name__,
                repr(fb_exc),
            )
            ids = []
        log.info(
            "provider.fallback_done provider=arxiv_api fallback=arxiv_search_html query={!r} count={}",
            cleaned,
            len(ids),
        )
        if ids:
            return ids

        # 2) Optional web-search fallback constrained to arXiv.
        if self._web_fallback is None:
            return []
        log.warning(
            "provider.fallback_start provider=arxiv_api fallback=web_search_arxiv reason={} query={!r}",
            reason,
            cleaned,
        )
        try:
            ids = await self._web_fallback.discover_arxiv_ids(cleaned, max_results)
        except Exception as fb_exc:  # pragma: no cover
            log.warning(
                "provider.fallback_failed provider=arxiv_api fallback=web_search_arxiv query={!r} error_type={} error_repr={}",
                cleaned,
                type(fb_exc).__name__,
                repr(fb_exc),
            )
            ids = []
        log.info(
            "provider.fallback_done provider=arxiv_api fallback=web_search_arxiv query={!r} count={}",
            cleaned,
            len(ids),
        )
        return ids

    async def discover_arxiv_ids_fallback(self, query: str, max_results: int, *, reason: str) -> list[str]:
        """Public entrypoint for fallback discovery (HTML -> web search)."""
        return await self._fallback_chain(query, max_results, reason=reason)

    def _build_params(self, *, query: str, max_results: int) -> dict[str, str]:
        sort_by = self._SORT_MAP.get(self._sort, "relevance")
        return {
            "search_query": f"all:{query}",
            "max_results": str(max_results),
            "sortBy": sort_by,
            "sortOrder": "descending",
        }

    @staticmethod
    def _retry_after_from_response(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header and header.strip().isdigit():
            return float(header.strip())
        return None

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(_BACKOFF_BASE_SECONDS * (2**attempt), 30.0)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.arxiv_api", query=query, max_results=max_results):
            cleaned = query.strip()
            if not cleaned or max_results <= 0:
                return []

            direct_id = extract_arxiv_id_from_text(cleaned, allow_bare=True)
            if direct_id:
                return [direct_id]

            cached = self._cache.get(cleaned) if self._cache is not None else None  # type: ignore[arg-type]
            if cached:
                log.info("provider.cache_hit provider=arxiv_api query={!r} count={}", cleaned, len(cached))
                return cached[:max_results]

            params = self._build_params(query=cleaned, max_results=max_results)
            headers = {"User-Agent": self._user_agent}

            # Be robust: try export endpoint first, then the main endpoint.
            # On each endpoint, retry a few times on transient failures (429/5xx/timeouts).
            last_exc: Exception | None = None
            timeout = httpx.Timeout(self._timeout_seconds) if self._timeout_seconds > 0 else None
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                for url in _ARXIV_API_URLS:
                    response: httpx.Response | None = None
                    for attempt in range(_MAX_RETRIES + 1):
                        try:
                            if self._rate_limiter is not None:
                                async with self._rate_limiter:
                                    pass
                            if self._timeout_seconds > 0:
                                response = await asyncio.wait_for(
                                    client.get(url, params=params, headers=headers),
                                    timeout=self._timeout_seconds,
                                )
                            else:
                                response = await client.get(url, params=params, headers=headers)
                        except TimeoutError as exc:
                            last_exc = exc
                            if attempt >= _MAX_RETRIES:
                                response = None
                                break
                            await asyncio.sleep(self._backoff_seconds(attempt))
                            continue
                        except httpx.RequestError as exc:
                            last_exc = exc
                            if attempt >= _MAX_RETRIES:
                                response = None
                                break
                            await asyncio.sleep(self._backoff_seconds(attempt))
                            continue

                        if response.status_code == 429 and attempt < _MAX_RETRIES:
                            retry_after = self._retry_after_from_response(response) or self._backoff_seconds(attempt)
                            await asyncio.sleep(min(retry_after, 30.0))
                            continue

                        if response.status_code in (500, 502, 503, 504) and attempt < _MAX_RETRIES:
                            await asyncio.sleep(self._backoff_seconds(attempt))
                            continue

                        break

                    if response is not None:
                        break
                else:
                    exc: DiscoveryAccessError
                    if isinstance(last_exc, TimeoutError):
                        exc = DiscoveryAccessError(
                            f"arXiv API request timed out after {self._timeout_seconds:.1f}s"
                        )
                    else:
                        exc = DiscoveryAccessError("arXiv API request failed")
                    ids = await self._fallback_chain(cleaned, max_results, reason="unavailable")
                    if ids:
                        return ids
                    raise exc from last_exc

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                preview = response.text[:240].replace("\n", " ")
                err = DiscoveryAccessError(
                    f"arXiv API request failed (status {response.status_code}). body={preview}"
                )
                if response.status_code == 429:
                    ids = await self._fallback_chain(cleaned, max_results, reason="rate_limited")
                    if ids:
                        return ids
                raise err from exc

            try:
                root = ET.fromstring(response.text)
            except ET.ParseError as exc:
                raise DiscoveryAccessError("arXiv API returned invalid XML") from exc

            ids: list[str] = []
            for entry in root.findall("a:entry", ARXIV_NS):
                raw_id = entry.findtext("a:id", default="", namespaces=ARXIV_NS).strip()
                if not raw_id:
                    continue
                arxiv_id = extract_arxiv_id_from_text(raw_id, allow_bare=True)
                if arxiv_id:
                    ids.append(arxiv_id)

            ids = dedupe_preserve(ids, max_results=max_results)
            log.info("done count={} ids={}", len(ids), ids)
            if self._cache is not None:
                self._cache[cleaned] = ids
                log.info("provider.cache_store provider=arxiv_api query={!r} count={}", cleaned, len(ids))
            return ids
