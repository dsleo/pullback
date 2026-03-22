"""Resolve OpenAlex result titles to arXiv IDs using arxiv metadata search."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

import arxiv

from ..observability import get_logger
from .parsing.arxiv_ids import extract_arxiv_id_from_text

log = get_logger("discovery.arxiv_resolver")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ArxivEntry:
    arxiv_id: str
    title: str


def normalize_title(text: str) -> str:
    lowered = text.lower()
    cleaned = NON_ALNUM_RE.sub(" ", lowered)
    return " ".join(cleaned.split())


def choose_best_arxiv_title_match(
    target_title: str,
    candidates: list[ArxivEntry],
    *,
    threshold: float = 0.90,
    ambiguity_margin: float = 0.02,
) -> str | None:
    if not candidates:
        return None

    target_norm = normalize_title(target_title)
    if not target_norm:
        return None

    scored: list[tuple[float, ArxivEntry]] = []
    for candidate in candidates:
        cand_norm = normalize_title(candidate.title)
        if not cand_norm:
            continue
        score = SequenceMatcher(None, target_norm, cand_norm).ratio()
        scored.append((score, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_entry = scored[0]
    if best_score < threshold:
        return None

    if len(scored) > 1:
        second_score = scored[1][0]
        if second_score >= best_score - ambiguity_margin and best_score < 0.97:
            return None

    return best_entry.arxiv_id


class ArxivTitleResolver:
    def __init__(
        self,
        *,
        query_max_results: int = 5,
        delay_seconds: float = 0.5,
        title_match_threshold: float = 0.90,
    ) -> None:
        self._query_max_results = max(1, query_max_results)
        self._delay_seconds = max(0.0, delay_seconds)
        self._title_match_threshold = title_match_threshold

    def _search_by_title_sync(self, title: str) -> list[ArxivEntry]:
        search = arxiv.Search(
            query=f'ti:"{title}"',
            max_results=self._query_max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        client = arxiv.Client()
        out: list[ArxivEntry] = []
        for result in client.results(search):
            arxiv_id = extract_arxiv_id_from_text(result.entry_id)
            if not arxiv_id:
                continue
            out.append(ArxivEntry(arxiv_id=arxiv_id, title=result.title))
        return out

    async def resolve_titles(self, titles: list[str], *, needed: int) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()

        for idx, title in enumerate(titles):
            if len(resolved) >= needed:
                break
            if idx > 0 and self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            try:
                candidates = await asyncio.to_thread(self._search_by_title_sync, title)
            except Exception as exc:  # pragma: no cover - network hard to unit test
                log.warning("title_resolve.query_failed title={} error={}", title, exc)
                continue
            match = choose_best_arxiv_title_match(
                title,
                candidates,
                threshold=self._title_match_threshold,
            )
            if not match or match in seen:
                continue
            seen.add(match)
            resolved.append(match)
            log.info("title_resolve.matched title={} arxiv_id={}", title, match)

        return resolved
