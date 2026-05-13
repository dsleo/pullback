"""Fetch paper metadata (title/authors) for arXiv IDs via arxiv.py."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import arxiv

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

    def _fetch(ids: list[str]) -> dict[str, PaperMetadata]:
        return fetch_arxiv_metadata_sync(ids, max_results_per_query=max(1, max_results_per_query))

    try:
        if timeout_seconds > 0:
            metadata = await asyncio.wait_for(
                asyncio.to_thread(_fetch, normalized_ids),
                timeout=max(0.0, timeout_seconds),
            )
        else:
            metadata = await asyncio.to_thread(_fetch, normalized_ids)
        log.info("metadata.done requested={} resolved={}", len(normalized_ids), len(metadata))
        return metadata
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
