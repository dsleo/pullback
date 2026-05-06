#!/usr/bin/env python
"""
Build a dataset of all raw theorem extractions (before strictness filtering).

This dataset stores all theorem blocks extracted by the forager agents,
allowing fast iteration on reranking techniques without re-running discovery/extraction.

Output format (JSONL):
{
  "query": "...",
  "gt_arxiv_id": "2310.15076",
  "theorems": [
    {
      "arxiv_id": "2310.15076",
      "line_number": 42,
      "header": "\\begin{theorem}",
      "snippet": "...",
      "score": 0.45
    },
    ...
  ]
}
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mathgent.api.deps import build_orchestrator
from mathgent.config import get_config
from mathgent.settings import load_settings
from mathgent.observability import get_logger

log = get_logger("build_theorem_dataset")


@dataclass
class BenchmarkItem:
    query: str
    gt_arxiv_id: str
    gt_theorem_label: str | None = None


@dataclass
class TheoremSnippet:
    arxiv_id: str
    line_number: int
    header: str
    snippet: str
    score: float


@dataclass
class QueryTheoremRecord:
    query: str
    gt_arxiv_id: str
    theorems: list[TheoremSnippet]


def _load_items(path: Path, limit: int | None) -> list[BenchmarkItem]:
    items: list[BenchmarkItem] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and len(items) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            query = str(payload.get("query", "")).strip()
            gt = str(payload.get("gt_arxiv_id", "")).strip()
            label = payload.get("gt_theorem_label")
            if not query or not gt:
                continue
            items.append(BenchmarkItem(query=query, gt_arxiv_id=gt, gt_theorem_label=label))
    return items


def _to_dict(record: QueryTheoremRecord) -> dict:
    return {
        "query": record.query,
        "gt_arxiv_id": record.gt_arxiv_id,
        "theorems": [
            {
                "arxiv_id": t.arxiv_id,
                "line_number": t.line_number,
                "header": t.header,
                "snippet": t.snippet,
                "score": round(t.score, 6),
            }
            for t in record.theorems
        ],
    }


def _write_jsonl(path: Path, rows: Iterable[QueryTheoremRecord]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_to_dict(row)) + "\n")


def _append_jsonl(path: Path, row: QueryTheoremRecord) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_to_dict(row)) + "\n")


async def _build_dataset(
    items: list[BenchmarkItem],
    *,
    max_results: int,
    output_path: Path | None = None,
) -> list[QueryTheoremRecord]:
    """Run queries and capture all theorem snippets before strictness filtering."""
    orchestrator = build_orchestrator()
    results: list[QueryTheoremRecord] = []

    # per-query theorem collection
    query_theorems: dict[str, list[TheoremSnippet]] = {}

    def _on_snippet_scored(plan, header, score, snippet, **kw):
        """Hook called for every snippet scored by the forager."""
        key = f"{plan.query}||{plan.arxiv_id}"
        if key not in query_theorems:
            query_theorems[key] = []
        query_theorems[key].append(
            TheoremSnippet(
                arxiv_id=plan.arxiv_id,
                line_number=header.line_number,
                header=header.line,
                snippet=snippet,
                score=float(score),
            )
        )

    # Register hook on all forager agent instances
    orchestrator.forager.on("snippet_scored", _on_snippet_scored)

    try:
        for idx, item in enumerate(items, start=1):
            query_theorems.clear()
            start = time.perf_counter()
            response = await orchestrator.search(
                query=item.query,
                max_results=max_results,
                strictness=0.0,  # No filtering, capture everything
            )
            latency = time.perf_counter() - start

            # Collect theorems for this query
            all_theorems: list[TheoremSnippet] = []
            for paper_arxiv_id in set(t.arxiv_id for ts in query_theorems.values() for t in ts):
                key = f"{item.query}||{paper_arxiv_id}"
                if key in query_theorems:
                    all_theorems.extend(query_theorems[key])

            # Sort by score descending
            all_theorems.sort(key=lambda t: t.score, reverse=True)

            result = QueryTheoremRecord(
                query=item.query,
                gt_arxiv_id=item.gt_arxiv_id,
                theorems=all_theorems,
            )
            results.append(result)
            if output_path is not None:
                _append_jsonl(output_path, result)

            n_theorems = len(all_theorems)
            print(
                f"[{idx}/{len(items)}] query='{item.query[:50]}...' "
                f"gt={item.gt_arxiv_id} theorems={n_theorems} latency={latency:.2f}s"
            )
    finally:
        orchestrator.close()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build dataset of all raw theorem extractions (before strictness filtering)"
    )
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Path to benchmark JSONL file (query, gt_arxiv_id, gt_theorem_label)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL file for theorem dataset",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Max discovery results to process",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N queries (for testing)",
    )

    args = parser.parse_args()

    if not args.data.exists():
        print(f"Error: --data file not found: {args.data}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading queries from {args.data}...")
    items = _load_items(args.data, limit=args.limit)
    print(f"Loaded {len(items)} queries")

    print(f"Building theorem dataset (max_results={args.max_results})...")
    asyncio.run(
        _build_dataset(
            items,
            max_results=args.max_results,
            output_path=output_path,
        )
    )

    print(f"Wrote theorem dataset to {output_path}")


if __name__ == "__main__":
    main()
