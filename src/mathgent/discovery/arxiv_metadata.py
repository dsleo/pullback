"""Fetch paper metadata (title/authors) for arXiv IDs via arxiv.py."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import arxiv

from ..observability import get_logger
from .parsing.arxiv_ids import extract_arxiv_id_from_text, normalize_arxiv_id

log = get_logger("discovery.arxiv_metadata")


@dataclass(frozen=True)
class PaperMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)


class PaperMetadataClient(Protocol):
    async def fetch_metadata(self, arxiv_ids: list[str]) -> dict[str, PaperMetadata]: ...


class ArxivMetadataClient(PaperMetadataClient):
    def __init__(self, *, max_results_per_query: int = 50) -> None:
        self._max_results_per_query = max(1, max_results_per_query)

    @staticmethod
    def _chunk(values: list[str], size: int) -> list[list[str]]:
        return [values[idx : idx + size] for idx in range(0, len(values), size)]

    @staticmethod
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

    def _fetch_sync(self, arxiv_ids: list[str]) -> dict[str, PaperMetadata]:
        client = arxiv.Client()
        out: dict[str, PaperMetadata] = {}
        for batch in self._chunk(arxiv_ids, self._max_results_per_query):
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
                title_raw = getattr(result, "title", None)
                title = " ".join(title_raw.split()) if isinstance(title_raw, str) and title_raw.strip() else None
                out[arxiv_id] = PaperMetadata(
                    title=title,
                    authors=self._authors_from_result(result),
                )
        return out

    async def fetch_metadata(self, arxiv_ids: list[str]) -> dict[str, PaperMetadata]:
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for raw in arxiv_ids:
            normalized = normalize_arxiv_id(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_ids.append(normalized)

        if not normalized_ids:
            return {}

        try:
            metadata = await asyncio.to_thread(self._fetch_sync, normalized_ids)
            log.info("metadata.done requested={} resolved={}", len(normalized_ids), len(metadata))
            return metadata
        except Exception as exc:  # pragma: no cover - network hard to unit test
            log.warning(
                "metadata.failed error_type={} error_repr={} requested={}",
                type(exc).__name__,
                repr(exc),
                len(normalized_ids),
            )
            return {}
