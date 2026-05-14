"""Fetch paper metadata (title/authors) for arXiv IDs via arxiv.py."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections import OrderedDict
import os
import re
import threading
import time
from typing import Awaitable, Callable

import arxiv
import httpx

from ...observability import get_logger
from .ids import extract_arxiv_id_from_text, normalize_arxiv_id

log = get_logger("discovery.arxiv_metadata")


@dataclass(frozen=True)
class PaperMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    cited_by_count: int | None = None


PaperMetadataFetcher = Callable[[list[str]], Awaitable[dict[str, PaperMetadata]]]

_DEFAULT_CACHE_TTL_S = 60 * 60 * 24  # 1 day
_DEFAULT_CACHE_MAX = 4000


class _TTLCache:
    """Tiny in-memory TTL cache for serverless environments (e.g. Vercel).

    It persists only for the lifetime of a warm instance. That's intentional:
    it is a light, dependency-free best-effort cache to reduce bursty upstream
    calls and rate-limiting.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._max_entries = max(0, int(max_entries))
        self._lock = threading.Lock()
        self._data: "OrderedDict[str, tuple[float, PaperMetadata]]" = OrderedDict()

    def get(self, key: str) -> PaperMetadata | None:
        if self._ttl_seconds <= 0 or self._max_entries <= 0:
            return None
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set_many(self, values: dict[str, PaperMetadata]) -> None:
        if self._ttl_seconds <= 0 or self._max_entries <= 0 or not values:
            return
        now = time.time()
        expires_at = now + self._ttl_seconds
        with self._lock:
            for k, v in values.items():
                self._data[k] = (expires_at, v)
                self._data.move_to_end(k)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)


_META_CACHE = _TTLCache(
    ttl_seconds=float(os.getenv("PULLBACK_METADATA_CACHE_TTL_S", str(_DEFAULT_CACHE_TTL_S))),
    max_entries=int(os.getenv("PULLBACK_METADATA_CACHE_MAX", str(_DEFAULT_CACHE_MAX))),
)


def normalize_dedup_arxiv_ids(arxiv_ids: list[str]) -> list[str]:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for raw in arxiv_ids:
        normalized = normalize_arxiv_id(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_ids.append(normalized)
    return normalized_ids


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())


def _authors_from_result(result: object) -> list[str]:
    authors = getattr(result, "authors", None)
    if not isinstance(authors, list):
        return []
    out: list[str] = []
    for author in authors:
        name = getattr(author, "name", None)
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


_META_RE_TEMPLATE = r'<meta\s+name="{name}"\s+content="(?P<value>[^"]+)"'


def _meta_values(html: str, name: str) -> list[str]:
    pattern = re.compile(_META_RE_TEMPLATE.format(name=re.escape(name)), re.IGNORECASE)
    return [match.group("value").strip() for match in pattern.finditer(html) if match.group("value").strip()]


def _metadata_from_abs_html(html: str) -> PaperMetadata | None:
    titles = _meta_values(html, "citation_title")
    authors = _meta_values(html, "citation_author")
    dates = _meta_values(html, "citation_date")
    title = _clean_text(titles[0]) if titles else None
    if not title or not authors:
        return None
    year = None
    if dates:
        year_match = re.match(r"(\d{4})", dates[0])
        if year_match:
            year = int(year_match.group(1))
    return PaperMetadata(title=title, authors=authors, year=year)


async def _fetch_abs_page_metadata(
    arxiv_ids: list[str],
    *,
    timeout_seconds: float,
) -> dict[str, PaperMetadata]:
    if not arxiv_ids:
        return {}

    out: dict[str, PaperMetadata] = {}
    sem = asyncio.Semaphore(4)
    per_request_timeout = max(2.0, min(timeout_seconds, 12.0))

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "pullback/0.1"},
        timeout=per_request_timeout,
    ) as client:
        async def _fetch_one(arxiv_id: str) -> tuple[str, PaperMetadata | None]:
            async with sem:
                try:
                    response = await client.get(f"https://arxiv.org/abs/{arxiv_id}")
                    response.raise_for_status()
                    return arxiv_id, _metadata_from_abs_html(response.text)
                except Exception as exc:  # pragma: no cover - network hard to unit test
                    log.warning(
                        "metadata.abs_failed arxiv_id={} error_type={} error_repr={}",
                        arxiv_id,
                        type(exc).__name__,
                        repr(exc),
                    )
                    return arxiv_id, None

        for arxiv_id, metadata in await asyncio.gather(*(_fetch_one(aid) for aid in arxiv_ids)):
            if metadata is not None:
                out[arxiv_id] = metadata

    return out


def fetch_arxiv_metadata_sync(
    arxiv_ids: list[str],
    *,
    max_results_per_query: int,
) -> dict[str, PaperMetadata]:
    batch_size = max(1, max_results_per_query)
    client = arxiv.Client()
    out: dict[str, PaperMetadata] = {}
    for idx in range(0, len(arxiv_ids), batch_size):
        batch = arxiv_ids[idx : idx + batch_size]
        search = arxiv.Search(
            id_list=batch,
            max_results=len(batch),
        )
        for result in client.results(search):
            entry_id = getattr(result, "entry_id", None)
            if not isinstance(entry_id, str):
                continue
            parsed = extract_arxiv_id_from_text(entry_id)
            if not parsed:
                continue
            arxiv_id = normalize_arxiv_id(parsed)
            published = getattr(result, "published", None)
            year = published.year if published is not None else None
            out[arxiv_id] = PaperMetadata(
                title=_clean_text(getattr(result, "title", None)),
                authors=_authors_from_result(result),
                year=year,
            )
    return out


async def fetch_arxiv_metadata(
    arxiv_ids: list[str],
    *,
    timeout_seconds: float = 15.0,
    max_results_per_query: int = 50,
) -> dict[str, PaperMetadata]:
    timeout_seconds = min(timeout_seconds, 15.0)
    normalized_ids = normalize_dedup_arxiv_ids(arxiv_ids)
    if not normalized_ids:
        return {}

    # Serve from cache first (best-effort; warm-instance only).
    out: dict[str, PaperMetadata] = {}
    unresolved: list[str] = []
    for arxiv_id in normalized_ids:
        cached = _META_CACHE.get(arxiv_id)
        if cached is not None:
            out[arxiv_id] = cached
        else:
            unresolved.append(arxiv_id)
    if not unresolved:
        log.info("metadata.done requested={} resolved={} (cache_hit)", len(normalized_ids), len(out))
        return out

    # Fast path: the arXiv export API is frequently rate-limited (429) under load.
    # Prefer fetching metadata from the arXiv abstract pages, which tends to be
    # more resilient, then only fall back to the export API for any remaining IDs.
    #
    # This reduces the number of export API calls and avoids long retry chains
    # when multiple users query concurrently.
    abs_meta = await _fetch_abs_page_metadata(unresolved, timeout_seconds=timeout_seconds)
    if abs_meta:
        out.update(abs_meta)
        _META_CACHE.set_many(abs_meta)
    unresolved = [arxiv_id for arxiv_id in unresolved if arxiv_id not in out]
    if not unresolved:
        log.info("metadata.done requested={} resolved={} (cache+abs)", len(normalized_ids), len(out))
        return out

    # Use smaller batches so a single 429 does not blank out metadata for the
    # whole set when arXiv is rate limiting.
    batch_size = max(1, min(max_results_per_query, 5))
    batches = [unresolved[i : i + batch_size] for i in range(0, len(unresolved), batch_size)]

    def _fetch(ids: list[str]) -> dict[str, PaperMetadata]:
        return fetch_arxiv_metadata_sync(ids, max_results_per_query=max(1, len(ids)))

    try:
        for batch in batches:
            try:
                if timeout_seconds > 0:
                    metadata = await asyncio.wait_for(
                        asyncio.to_thread(_fetch, batch),
                        timeout=max(0.0, timeout_seconds),
                    )
                else:
                    metadata = await asyncio.to_thread(_fetch, batch)
                out.update(metadata)
                _META_CACHE.set_many(metadata)
            except asyncio.TimeoutError:
                log.warning("metadata.batch_timeout batch_size={} timeout_s={}", len(batch), timeout_seconds)
            except Exception as exc:  # pragma: no cover - network hard to unit test
                log.warning(
                    "metadata.batch_failed error_type={} error_repr={} batch_size={}",
                    type(exc).__name__,
                    repr(exc),
                    len(batch),
                )

        log.info("metadata.done requested={} resolved={}", len(normalized_ids), len(out))
        return out
    except asyncio.TimeoutError:
        log.warning("metadata.timeout requested={} timeout_s={}", len(normalized_ids), timeout_seconds)
        return {}
    except Exception as exc:  # pragma: no cover - network hard to unit test
        log.warning(
            "metadata.failed error_type={} error_repr={} requested={}",
            type(exc).__name__,
            repr(exc),
            len(normalized_ids),
        )
        return {}
