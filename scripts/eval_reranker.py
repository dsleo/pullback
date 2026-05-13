#!/usr/bin/env python
"""
Fast offline evaluation of reranker strategies on the theorem dataset.

Reads cached theorem snippets (no network calls), re-scores with different strategies,
and computes recall@k and MRR for ground truth theorem ranking.

Usage:
  python scripts/eval_reranker.py --dataset data/theorem_dataset_71_enriched.jsonl --strategy token
  python scripts/eval_reranker.py --dataset data/theorem_dataset_71_enriched.jsonl --strategy hybrid
  python scripts/eval_reranker.py --dataset data/theorem_dataset_71_enriched.jsonl --strategy bge
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pullback.rerank import create_reranker
from pullback.observability import get_logger
from pullback.settings import load_settings

log = get_logger("eval_reranker")


@dataclass
class TheoremResult:
    arxiv_id: str
    line_number: int
    header: str
    snippet: str
    score: float
    is_gt: bool = False


@dataclass
class QueryResult:
    query: str
    gt_arxiv_id: str
    theorems: list[TheoremResult]
    gt_label_found: bool = False
    gt_in_candidates: bool = False


def _load_enriched_dataset(path: Path) -> list[QueryResult]:
    """Load enriched theorem dataset."""
    results = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            theorems = [
                TheoremResult(
                    arxiv_id=t["arxiv_id"],
                    line_number=t["line_number"],
                    header=t["header"],
                    snippet=t["snippet"],
                    score=t["score"],
                    is_gt=t.get("is_gt", False),
                )
                for t in payload.get("theorems", [])
            ]
            results.append(
                QueryResult(
                    query=payload["query"],
                    gt_arxiv_id=payload["gt_arxiv_id"],
                    theorems=theorems,
                    gt_label_found=payload.get("gt_label_found", False),
                    gt_in_candidates=payload.get("gt_in_candidates", False),
                )
            )
    return results


@dataclass
class EvalMetrics:
    strategy: str
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    mrr: float
    gt_scored: float
    total_queries: int
    gt_found: int
    gt_rank_mean: Optional[float]


def _evaluate_reranker(
    results: list[QueryResult],
    strategy: str = "token",
    bge_model: str = "BAAI/bge-reranker-v2-m3",
    biencoder_model: str = "all-MiniLM-L6-v2",
    min_overlap: float = 0.01,
    limit_per_query: int = 0,  # 0 = no limit
) -> EvalMetrics:
    """Evaluate a reranker strategy.

    Args:
        limit_per_query: For expensive models like BGE, limit snippets scored per query (0 = unlimited).
                         e.g., 100 scores only top 100 snippets per query.
    """

    settings = load_settings()
    reranker = create_reranker(
        strategy,
        bge_model=bge_model,
        biencoder_model=biencoder_model,
        openrouter_model=settings.rerank.openrouter_model,
        api_key=settings.rerank.api_key,
    )
    log.info("Evaluating strategy={} on {} queries (limit_per_query={})", strategy, len(results), limit_per_query)

    recall_1_hits = 0
    recall_5_hits = 0
    recall_10_hits = 0
    recall_20_hits = 0
    mrr_sum = 0.0
    gt_scored_count = 0
    gt_rank_list = []

    for idx, query_result in enumerate(results, start=1):
        query = query_result.query
        theorems = query_result.theorems

        # For expensive models, pre-sort by score to limit top-k
        if limit_per_query > 0:
            theorems.sort(key=lambda t: t.score, reverse=True)
            theorems_to_score = theorems[:limit_per_query]
            unscored_rest = theorems[limit_per_query:]
        else:
            theorems_to_score = theorems
            unscored_rest = []

        # Re-score top-k (or all if unlimited)
        snippets = [t.snippet for t in theorems_to_score]
        scores = reranker.score_batch(query, snippets)
        for theorem, score in zip(theorems_to_score, scores):
            theorem.score = score

        # Re-sort: rescored items first (descending), then unscored at end
        if unscored_rest:
            theorems_to_score.sort(key=lambda t: t.score, reverse=True)
            query_result.theorems = theorems_to_score + unscored_rest
        else:
            query_result.theorems.sort(key=lambda t: t.score, reverse=True)

        # Find gt theorem rank
        gt_rank = None
        for rank, theorem in enumerate(query_result.theorems, start=1):
            if theorem.is_gt:
                gt_rank = rank
                break

        if gt_rank is not None:
            gt_scored_count += 1
            gt_rank_list.append(gt_rank)
            if gt_rank <= 1:
                recall_1_hits += 1
            if gt_rank <= 5:
                recall_5_hits += 1
            if gt_rank <= 10:
                recall_10_hits += 1
            if gt_rank <= 20:
                recall_20_hits += 1
            mrr_sum += 1.0 / gt_rank
            status = f"✓rank={gt_rank}"
        else:
            status = "✗not_scored"

        if idx % 10 == 0 or idx == len(results):
            print(f"  [{idx}/{len(results)}] {status}")

    total = len(results)
    metrics = EvalMetrics(
        strategy=strategy,
        recall_at_1=recall_1_hits / total * 100.0 if total else 0.0,
        recall_at_5=recall_5_hits / total * 100.0 if total else 0.0,
        recall_at_10=recall_10_hits / total * 100.0 if total else 0.0,
        recall_at_20=recall_20_hits / total * 100.0 if total else 0.0,
        mrr=mrr_sum / total if total else 0.0,
        gt_scored=gt_scored_count / total * 100.0 if total else 0.0,
        total_queries=total,
        gt_found=gt_scored_count,
        gt_rank_mean=sum(gt_rank_list) / len(gt_rank_list) if gt_rank_list else None,
    )

    return metrics


def _print_metrics(metrics: EvalMetrics) -> str:
    """Format metrics as a readable table row."""
    mean_rank = f"{metrics.gt_rank_mean:6.1f}" if metrics.gt_rank_mean is not None else "   N/A"
    return (
        f"| {metrics.strategy:15} | {metrics.recall_at_1:6.2f}% | {metrics.recall_at_5:6.2f}% | "
        f"{metrics.recall_at_10:6.2f}% | {metrics.recall_at_20:6.2f}% | {metrics.mrr:7.4f} | "
        f"{metrics.gt_scored:5.1f}% | {mean_rank} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate reranker strategies on cached theorem dataset")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/theorem_dataset_71_enriched.jsonl"),
        help="Path to enriched theorem dataset",
    )
    parser.add_argument(
        "--strategy",
        choices=["token", "bge", "biencoder", "hybrid", "colbert", "openrouter", "cohere"],
        default="token",
        help="Reranker strategy to evaluate",
    )
    parser.add_argument(
        "--bge-model",
        default="BAAI/bge-reranker-v2-m3",
        help="BGE model name",
    )
    parser.add_argument(
        "--biencoder-model",
        default="all-MiniLM-L6-v2",
        help="BiEncoder model name (for CPU-efficient semantic ranking)",
    )
    parser.add_argument(
        "--min-overlap",
        type=float,
        default=0.01,
        help="Min token overlap for hybrid strategy (0.0-1.0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N queries for testing",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=0,
        help="For slow models like BGE, limit snippets scored per query (0=unlimited)",
    )

    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"Error: dataset not found: {args.dataset}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading dataset from {args.dataset}...")
    results = _load_enriched_dataset(args.dataset)
    if args.limit:
        results = results[: args.limit]
    print(f"Loaded {len(results)} queries")

    # Check for gt marking
    with_gt = sum(1 for r in results if any(t.is_gt for t in r.theorems))
    print(f"Queries with gt marked: {with_gt}/{len(results)}")

    print(f"\nEvaluating strategy={args.strategy}...")
    metrics = _evaluate_reranker(
        results,
        strategy=args.strategy,
        bge_model=args.bge_model,
        biencoder_model=args.biencoder_model,
        min_overlap=args.min_overlap,
        limit_per_query=args.limit_per_query,
    )

    print("\n" + "=" * 110)
    print(
        "| Strategy        | Recall@1 | Recall@5 | Recall@10 | Recall@20 |    MRR   | GT Scored | Mean Rank |"
    )
    print("| " + "-" * 107 + " |")
    print(_print_metrics(metrics))
    print("=" * 110)

    # Write to markdown
    results_dir = Path("experiments")
    results_dir.mkdir(exist_ok=True)
    md_file = results_dir / "reranker_experiments.md"

    # Create header if file doesn't exist
    if not md_file.exists():
        with md_file.open("w") as f:
            f.write("# Reranker Evaluation Experiments\n\n")
            f.write("| Strategy        | Recall@1 | Recall@5 | Recall@10 | Recall@20 |    MRR   | GT Scored | Mean Rank |\n")
            f.write("| " + "-" * 107 + " |\n")

    # Append results
    with md_file.open("a") as f:
        f.write(_print_metrics(metrics) + "\n")

    print(f"\nAppended to {md_file}")


if __name__ == "__main__":
    main()
