from __future__ import annotations

import asyncio
import re
from typing import Iterable

import httpx
from aiolimiter import AsyncLimiter
from cachetools import TTLCache
from selectolax.lexbor import LexborHTMLParser

from ...observability import get_logger, trace_span
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text
from ..base import DiscoveryAccessError, PaperDiscoveryClient

log = get_logger("discovery.arxiv_search_html")
_ARXIV_SEARCH_URL = "https://arxiv.org/search/"

_DEFAULT_CACHE = TTLCache(maxsize=4096, ttl=6 * 60 * 60)


def _default_limiter() -> AsyncLimiter:
    # Create a limiter per event loop to avoid cross-loop reuse issues.
    return AsyncLimiter(1, 0.5)  # ~2 requests/second per process


def _extract_ids_from_hrefs(hrefs: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for href in hrefs:
        candidate = href
        if "/abs/" in candidate:
            candidate = candidate.split("/abs/", 1)[1]
        # Strip common version suffixes.
        candidate = re.sub(r"v\d+$", "", candidate)
        arxiv_id = extract_arxiv_id_from_text(candidate, allow_bare=True)
        if arxiv_id:
            ids.append(arxiv_id)
    return ids


def parse_arxiv_search_html(html: str, *, max_results: int) -> list[str]:
    if not html or max_results <= 0:
        return []
    parser = LexborHTMLParser(html)
    hrefs: list[str] = []
    for node in parser.css("a[href]"):
        href = node.attributes.get("href")
        if not href:
            continue
        if "/abs/" not in href:
            continue
        hrefs.append(href)
    ids = _extract_ids_from_hrefs(hrefs)
    return dedupe_preserve(ids, max_results=max_results)


class ArxivSearchHtmlDiscoveryClient(PaperDiscoveryClient):
    """arXiv search HTML fallback provider.

    Used when the Atom export API is rate-limited/unavailable. This is still
    subject to arXiv throttling, so callers should share a limiter+cache.
    """

    _MAX_RETRIES = 2
    _BACKOFF_BASE_SECONDS = 1.0

    def __init__(
        self,
        *,
        timeout_seconds: float = 12.0,
        user_agent: str = "pullback/0.1",
        cache: TTLCache[str, list[str]] | None = None,
        rate_limiter: AsyncLimiter | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._cache = _DEFAULT_CACHE if cache is None else cache
        self._rate_limiter = _default_limiter() if rate_limiter is None else rate_limiter

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        return min(ArxivSearchHtmlDiscoveryClient._BACKOFF_BASE_SECONDS * (2**attempt), 10.0)

    @staticmethod
    def _retry_after_from_response(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header and header.strip().isdigit():
            return float(header.strip())
        return None

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.arxiv_search_html", query=query, max_results=max_results):
            cleaned = query.strip()
            if not cleaned or max_results <= 0:
                return []

            direct_id = extract_arxiv_id_from_text(cleaned, allow_bare=True)
            if direct_id:
                return [direct_id]

            if self._cache:
                cached = self._cache.get(cleaned)  # type: ignore[arg-type]
                if cached:
                    log.info("provider.cache_hit provider=arxiv_search_html query={!r} count={}", cleaned, len(cached))
                    return cached[:max_results]

            params = {
                "query": cleaned,
                "searchtype": "all",
                "abstracts": "show",
                "order": "-announced_date_first",
                "size": str(min(max_results, 50)),
            }

            headers = {"User-Agent": self._user_agent}
            timeout = httpx.Timeout(self._timeout_seconds) if self._timeout_seconds > 0 else None

            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                last_exc: Exception | None = None
                for attempt in range(self._MAX_RETRIES + 1):
                    try:
                        if self._rate_limiter is not None:
                            async with self._rate_limiter:
                                pass
                        if self._timeout_seconds > 0:
                            response = await asyncio.wait_for(
                                client.get(_ARXIV_SEARCH_URL, params=params, headers=headers),
                                timeout=self._timeout_seconds,
                            )
                        else:
                            response = await client.get(_ARXIV_SEARCH_URL, params=params, headers=headers)
                    except TimeoutError as exc:
                        last_exc = exc
                        if attempt >= self._MAX_RETRIES:
                            raise DiscoveryAccessError(
                                f"arXiv search HTML request timed out after {self._timeout_seconds:.1f}s"
                            ) from exc
                        await asyncio.sleep(self._backoff_seconds(attempt))
                        continue
                    except httpx.RequestError as exc:
                        last_exc = exc
                        if attempt >= self._MAX_RETRIES:
                            raise DiscoveryAccessError("arXiv search HTML request failed") from exc
                        await asyncio.sleep(self._backoff_seconds(attempt))
                        continue

                    if response.status_code == 429 and attempt < self._MAX_RETRIES:
                        retry_after = self._retry_after_from_response(response) or self._backoff_seconds(attempt)
                        await asyncio.sleep(min(retry_after, 10.0))
                        continue

                    # arXiv occasionally returns 400 with an HTML body; treat as empty rather than hard-fail.
                    if response.status_code == 400:
                        log.warning(
                            "provider.degraded provider=arxiv_search_html status=400 treating_as_empty query={!r} url={!r}",
                            cleaned,
                            str(response.url),
                        )
                        ids = []
                        if self._cache is not None:
                            self._cache[cleaned] = ids
                            log.info(
                                "provider.cache_store provider=arxiv_search_html query={!r} count={}",
                                cleaned,
                                len(ids),
                            )
                        return []

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        preview = response.text[:240].replace("\n", " ")
                        raise DiscoveryAccessError(
                            f"arXiv search HTML request failed (status {response.status_code}). body={preview}"
                        ) from exc

                    ids = parse_arxiv_search_html(response.text, max_results=max_results)
                    ids = dedupe_preserve(ids, max_results=max_results)
                    log.info("done count={} ids={}", len(ids), ids)
                    if self._cache is not None:
                        self._cache[cleaned] = ids
                        log.info("provider.cache_store provider=arxiv_search_html query={!r} count={}", cleaned, len(ids))
                    return ids

                raise DiscoveryAccessError("arXiv search HTML exhausted retries") from last_exc
