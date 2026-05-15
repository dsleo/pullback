from __future__ import annotations

import asyncio
import re

import httpx
from aiolimiter import AsyncLimiter
from cachetools import TTLCache
from selectolax.lexbor import LexborHTMLParser

from ....observability import get_logger
from ..ids import dedupe_preserve
from ...providers.web_search_arxiv import WebSearchArxivDiscoveryClient
from ...providers.arxiv_search_html import ArxivSearchHtmlDiscoveryClient

log = get_logger("discovery.arxiv_title_resolver")

_DEFAULT_ABS_CACHE = TTLCache(maxsize=4096, ttl=6 * 60 * 60)


def _default_limiter() -> AsyncLimiter:
    return AsyncLimiter(1, 0.5)


def _norm_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _extract_abs_title(html: str) -> str | None:
    parser = LexborHTMLParser(html)
    meta = parser.css_first('meta[name="citation_title"]')
    if meta is not None:
        content = meta.attributes.get("content")
        if content:
            return " ".join(content.split())
    og = parser.css_first('meta[property="og:title"]')
    if og is not None:
        content = og.attributes.get("content")
        if content:
            return " ".join(content.split())
    return None


async def _fetch_abs_title(
    arxiv_id: str,
    *,
    timeout_seconds: float,
    limiter: AsyncLimiter | None,
    cache: TTLCache[str, str] | None,
) -> str | None:
    if cache is not None:
        cached = cache.get(arxiv_id)
        if cached:
            return cached

    url = f"https://arxiv.org/abs/{arxiv_id}"
    timeout = httpx.Timeout(timeout_seconds) if timeout_seconds > 0 else None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if limiter is not None:
            async with limiter:
                pass
        resp = await client.get(url, headers={"User-Agent": "pullback/0.1"})
        resp.raise_for_status()
        title = _extract_abs_title(resp.text)
        if title and cache is not None:
            cache[arxiv_id] = title
        return title


async def _pick_verified_id(
    candidate_ids: list[str],
    *,
    wanted_title: str,
    timeout_seconds: float,
    limiter: AsyncLimiter | None,
    abs_title_cache: TTLCache[str, str] | None,
) -> str | None:
    for candidate_id in candidate_ids:
        try:
            abs_title = await _fetch_abs_title(
                candidate_id,
                timeout_seconds=timeout_seconds,
                limiter=limiter,
                cache=abs_title_cache,
            )
        except Exception:
            continue
        if abs_title and _norm_title(abs_title) == wanted_title:
            return candidate_id
    return None


async def resolve_titles_to_arxiv_ids(
    titles: list[str],
    *,
    max_results: int,
    timeout_seconds: float = 10.0,
    limiter: AsyncLimiter | None = None,
    query_cache: TTLCache[str, list[str]] | None = None,
    abs_title_cache: TTLCache[str, str] | None = None,
    web_search: WebSearchArxivDiscoveryClient | None = None,
) -> list[str]:
    """Resolve candidate paper titles to arXiv IDs.

    Conservative: searches only a small prefix of candidate titles and verifies
    accepted IDs against the arXiv abstract page title before returning them.
    """
    if max_results <= 0:
        return []
    cleaned_titles = [t for t in (" ".join((t or "").split()) for t in titles) if t]
    if not cleaned_titles:
        return []

    limiter = limiter or _default_limiter()
    abs_title_cache = abs_title_cache or _DEFAULT_ABS_CACHE

    search_client = ArxivSearchHtmlDiscoveryClient(
        timeout_seconds=timeout_seconds,
        cache=query_cache,
        rate_limiter=limiter,
    )

    resolved: list[str] = []
    # Keep it tight: resolving many titles can create extra load.
    for title in cleaned_titles[:5]:
        query = f"\"{title}\""
        # Prefer web search when available: it reduces direct arXiv load.
        if web_search is not None:
            log.warning(
                "provider.fallback_start provider=arxiv_title_resolver fallback=web_search_arxiv reason=prefer_web_search title={!r}",
                title,
            )
            try:
                ids = await web_search.discover_arxiv_ids(query, max_results=5)
            except Exception as exc:
                log.warning(
                    "provider.fallback_failed provider=arxiv_title_resolver fallback=web_search_arxiv title={!r} error_type={} error_repr={}",
                    title,
                    type(exc).__name__,
                    repr(exc),
                )
                ids = []
            log.info(
                "provider.fallback_done provider=arxiv_title_resolver fallback=web_search_arxiv title={!r} count={}",
                title,
                len(ids),
            )
            if ids:
                picked = await _pick_verified_id(
                    ids,
                    wanted_title=_norm_title(title),
                    timeout_seconds=timeout_seconds,
                    limiter=limiter,
                    abs_title_cache=abs_title_cache,
                )
                if picked:
                    resolved.append(picked)
                    if len(resolved) >= max_results:
                        break
                    continue

        try:
            ids = await search_client.discover_arxiv_ids(query, max_results=5)
        except Exception as exc:
            log.warning(
                "title_search.failed title={!r} error_type={} error_repr={}",
                title,
                type(exc).__name__,
                repr(exc),
            )
            continue
        if not ids:
            continue

        picked = await _pick_verified_id(
            ids,
            wanted_title=_norm_title(title),
            timeout_seconds=timeout_seconds,
            limiter=limiter,
            abs_title_cache=abs_title_cache,
        )
        if picked:
            resolved.append(picked)
            if len(resolved) >= max_results:
                break

    return dedupe_preserve(resolved, max_results=max_results)

