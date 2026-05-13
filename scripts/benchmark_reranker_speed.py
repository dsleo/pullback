#!/usr/bin/env python
"""
Benchmark reranker speed on a single query to estimate full evaluation time.
"""

import json
import time
from pathlib import Path

from pullback.rerank import create_reranker


def main() -> None:
    # Load first query
    with open("data/theorem_dataset_71_enriched.jsonl") as f:
        first_record = json.loads(f.readline())

    query = first_record["query"]
    theorems = first_record["theorems"]
    n_theorems = len(theorems)

    print(f"Query: {query[:80]}")
    print(f"Number of theorems: {n_theorems}")
    print()

    # Test each strategy
    for strategy in ["token", "bge", "hybrid"]:
        print(f"\n{'='*60}")
        print(f"Strategy: {strategy.upper()}")
        print(f"{'='*60}")

        reranker = create_reranker(strategy, bge_model="BAAI/bge-reranker-v2-m3")

        # Score all theorems
        start = time.perf_counter()
        scores = []
        for i, theorem in enumerate(theorems, start=1):
            score = reranker.score(query, theorem["snippet"])
            scores.append(score)
            if i % 100 == 0 or i == n_theorems:
                elapsed = time.perf_counter() - start
                per_snippet = elapsed / i
                eta = per_snippet * (n_theorems - i)
                print(f"  [{i}/{n_theorems}] elapsed={elapsed:.2f}s per_snippet={per_snippet*1000:.2f}ms eta={eta:.1f}s")

        total_time = time.perf_counter() - start
        per_snippet_ms = (total_time / n_theorems) * 1000

        print(f"\nTotal time for {n_theorems} theorems: {total_time:.2f}s")
        print(f"Time per theorem: {per_snippet_ms:.2f}ms")
        print(f"Estimated time for 71 queries: {total_time * 71:.1f}s ({total_time * 71 / 60:.1f}min)")


if __name__ == "__main__":
    main()
