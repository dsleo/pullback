#!/usr/bin/env python
"""
Evaluate reranker on pre-extracted theorem dataset.

This script tests reranker implementations against the theorem_dataset_71_enriched.jsonl,
which contains pre-extracted theorems from papers. Unlike full benchmarks, this skips
discovery and focuses purely on reranking evaluation.

Usage:
    python scripts/eval_reranker_theorems.py \
        --data data/theorem_dataset_71_enriched.jsonl \
        --reranker token \
        --output logs/reranker_theorem_token.jsonl
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mathgent.config import get_config
from mathgent.rerank.factory import create_reranker


@dataclass
class TheoremItem:
    """A single item in the theorem dataset."""
    query: str
    gt_arxiv_id: str
    theorems: list[dict]  # list of {arxiv_id, line_number, header, snippet, score}


@dataclass
class RerankerResult:
    """Result of reranking a single query's theorems."""
    query: str
    gt_arxiv_id: str
    num_theorems: int
    top_k: int
    hit: bool
    top_k_arxiv_ids: list[str]
    timestamp: float


def _load_items(path: Path, limit: int | None) -> list[TheoremItem]:
    """Load theorem items from JSONL file."""
    items = []
    with open(path) as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            data = json.loads(line)
            items.append(TheoremItem(
                query=data["query"],
                gt_arxiv_id=data["gt_arxiv_id"],
                theorems=data["theorems"]
            ))
    return items


def _result_payload(row: RerankerResult) -> dict:
    """Convert result to JSON-serializable dict."""
    return {
        "query": row.query,
        "gt_arxiv_id": row.gt_arxiv_id,
        "num_theorems": row.num_theorems,
        "top_k": row.top_k,
        "hit": row.hit,
        "top_k_arxiv_ids": row.top_k_arxiv_ids,
        "timestamp": row.timestamp,
    }


def _append_jsonl(path: Path, row: RerankerResult) -> None:
    """Append single result to JSONL file."""
    with open(path, "a") as f:
        f.write(json.dumps(_result_payload(row)) + "\n")


def _run_evaluation(
    items: list[TheoremItem],
    reranker_name: str,
    output_path: Path,
    top_k: int = 20,
) -> list[RerankerResult]:
    """Evaluate reranker on theorem dataset."""
    config = get_config()
    # Get reranker-specific config if needed
    kwargs = {}
    if reranker_name == "openrouter":
        # config is a dict, extract reranker and openrouter settings if present
        if config.get("rerank", {}).get("openrouter_model"):
            kwargs["openrouter_model"] = config["rerank"]["openrouter_model"]
        if config.get("openrouter_api_key"):
            kwargs["api_key"] = config["openrouter_api_key"]

    reranker = create_reranker(reranker_name, **kwargs)

    results = []
    start_time = time.time()

    for i, item in enumerate(items):
        # Prepare snippets and scores
        snippets = [t["snippet"] for t in item.theorems]

        if not snippets:
            print(f"[{i+1}/{len(items)}] Skipping query with no theorems: {item.query[:60]}")
            continue

        # Score all snippets
        scores = reranker.score_batch(item.query, snippets)

        # Rank by score
        ranked = sorted(
            zip(item.theorems, scores),
            key=lambda x: x[1],
            reverse=True
        )

        # Top-k check
        top_k_papers = [t[0]["arxiv_id"] for t in ranked[:top_k]]
        hit = item.gt_arxiv_id in top_k_papers

        result = RerankerResult(
            query=item.query,
            gt_arxiv_id=item.gt_arxiv_id,
            num_theorems=len(item.theorems),
            top_k=top_k,
            hit=hit,
            top_k_arxiv_ids=top_k_papers,
            timestamp=time.time(),
        )
        results.append(result)

        # Append incrementally
        _append_jsonl(output_path, result)

        elapsed = time.time() - start_time
        avg_per_item = elapsed / (i + 1)
        remaining = (len(items) - i - 1) * avg_per_item

        status = "✓" if hit else "✗"
        print(
            f"[{i+1}/{len(items)}] {status} {item.query[:60]:<60} "
            f"| {len(item.theorems):3d} snippets | ETA {remaining:.1f}s"
        )

    return results


def _summarize(results: list[RerankerResult]) -> None:
    """Print summary statistics."""
    if not results:
        print("No results to summarize.")
        return

    total = len(results)
    hits = sum(1 for r in results if r.hit)
    hit_rate = 100 * hits / total if total > 0 else 0

    print("\n" + "=" * 60)
    print(f"Reranker Evaluation Summary")
    print("=" * 60)
    print(f"Total queries:     {total}")
    print(f"Hits (top-{results[0].top_k}):       {hits}/{total} ({hit_rate:.1f}%)")
    print("=" * 60)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Evaluate reranker on pre-extracted theorem dataset."
    )
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Path to theorem dataset JSONL file",
    )
    parser.add_argument(
        "--reranker",
        type=str,
        default="token",
        choices=["token", "bge", "colbert", "openrouter"],
        help="Reranker strategy to evaluate",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/reranker_theorem_eval.jsonl"),
        help="Output JSONL file for results",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Top-k threshold for hit evaluation",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of items to process",
    )

    args = parser.parse_args()

    # Create output directory
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Clear previous results if file exists
    if args.output.exists():
        args.output.unlink()

    # Load items
    print(f"Loading theorem dataset from {args.data}...")
    items = _load_items(args.data, args.limit)
    print(f"Loaded {len(items)} queries with pre-extracted theorems")

    # Run evaluation
    print(f"\nEvaluating with reranker: {args.reranker}")
    try:
        results = _run_evaluation(items, args.reranker, args.output, top_k=args.top_k)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError during evaluation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Print summary
    _summarize(results)
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
