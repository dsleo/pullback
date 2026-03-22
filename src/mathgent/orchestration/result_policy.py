"""Result merge and ranking policy for librarian search output."""

from __future__ import annotations

from ..models import SearchResultEntry

IndexedResult = tuple[int, SearchResultEntry]


class ResultPolicy:
    @staticmethod
    def merge_indexed_results(
        *,
        aggregate_results: dict[str, IndexedResult],
        incoming_results: list[IndexedResult],
        next_index: int,
    ) -> int:
        for _, entry in incoming_results:
            existing = aggregate_results.get(entry.arxiv_id)
            if existing is None:
                aggregate_results[entry.arxiv_id] = (next_index, entry)
                next_index += 1
                continue

            _, current_entry = existing
            if entry.match is None:
                continue
            if current_entry.match is None or entry.match.score > current_entry.match.score:
                aggregate_results[entry.arxiv_id] = (existing[0], entry)

        return next_index

    @staticmethod
    def rank_and_trim_results(
        *,
        indexed_results: list[IndexedResult],
        max_results: int,
    ) -> list[SearchResultEntry]:
        indexed_results.sort(key=lambda item: item[0])
        ordered_results = [item[1] for item in indexed_results]
        matched_results = [item for item in ordered_results if item.match is not None]
        matched_results.sort(key=lambda item: item.match.score if item.match else -1.0, reverse=True)
        unmatched_results = [item for item in ordered_results if item.match is None]
        return (matched_results + unmatched_results)[:max_results]
