#!/usr/bin/env python
"""Save iteration config snapshot and log metrics to ITERATIONS.jsonl."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract_metrics_from_benchmark(benchmark_output_path: Path) -> dict:
    """Extract metrics from benchmark JSONL output.

    The benchmark output contains per-query results. This function calculates
    aggregated metrics (paper@20, theorem@20, avg_latency_s, mrr) from the results.
    """
    try:
        with open(benchmark_output_path) as f:
            lines = f.readlines()

        results = []
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                results.append(data)
            except json.JSONDecodeError:
                continue

        if not results:
            raise ValueError("No valid results found in benchmark output")

        # Calculate metrics (same logic as eval_benchmark.py::_summarize)
        total = len(results)

        # paper@20: count results where gt_arxiv_id is in top_ids[:20]
        paper20_hits = sum(
            1 for r in results
            if r.get("gt_arxiv_id") in r.get("top_ids", [])[:20]
        )
        paper20_ratio = paper20_hits / total if total else 0.0

        # theorem@20: count results where theorem_hit is True
        theorem20_hits = sum(1 for r in results if r.get("theorem_hit", False))
        theorem20_ratio = theorem20_hits / total if total else 0.0

        # avg_latency_s: mean of latency_s
        avg_latency = (
            sum(r.get("latency_s", 0.0) for r in results) / total if total else 0.0
        )

        # mrr: mean reciprocal rank
        mrr = (
            sum(1.0 / r.get("rank") for r in results if r.get("rank"))
            / total
            if total
            else 0.0
        )

        return {
            "paper@20": paper20_ratio,
            "theorem@20": theorem20_ratio,
            "avg_latency_s": avg_latency,
            "mrr": mrr,
            "total_queries": total,
        }
    except Exception as e:
        raise ValueError(f"Failed to extract metrics from {benchmark_output_path}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Save iteration config and log metrics")
    parser.add_argument("iteration", type=int, help="Iteration number")
    parser.add_argument("config_name", help="Config description (e.g., 'top_k_15', 'concurrency_8')")
    parser.add_argument("benchmark_output", help="Path to benchmark output JSONL file")
    parser.add_argument("--hypothesis", default="", help="Hypothesis for this iteration")
    parser.add_argument("--status", default="PENDING", choices=["PENDING", "ACCEPT", "REVERT"],
                        help="Iteration status")
    args = parser.parse_args()

    benchmark_path = Path(args.benchmark_output)
    if not benchmark_path.exists():
        print(f"Error: benchmark output file not found: {benchmark_path}", file=sys.stderr)
        sys.exit(1)

    # Extract metrics
    try:
        metrics = extract_metrics_from_benchmark(benchmark_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Save config snapshot
    config_path = ROOT / "config.json"
    snapshot_path = ROOT / "configs" / f"iteration_{args.iteration}_{args.config_name}.json"

    if not config_path.exists():
        print(f"Error: config.json not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    try:
        shutil.copy2(config_path, snapshot_path)
        print(f"✓ Config snapshot saved: {snapshot_path.relative_to(ROOT)}")
    except Exception as e:
        print(f"Error: Failed to save config snapshot: {e}", file=sys.stderr)
        sys.exit(1)

    # Append to ITERATIONS.jsonl
    iterations_log = ROOT / "configs" / "ITERATIONS.jsonl"
    iterations_log.parent.mkdir(parents=True, exist_ok=True)

    iteration_entry = {
        "iteration": args.iteration,
        "config_file": f"configs/iteration_{args.iteration}_{args.config_name}.json",
        "config_name": args.config_name,
        "hypothesis": args.hypothesis,
        "paper@20": metrics["paper@20"],
        "theorem@20": metrics["theorem@20"],
        "avg_latency_s": metrics["avg_latency_s"],
        "mrr": metrics.get("mrr", 0.0),
        "status": args.status,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_queries": metrics.get("total_queries", 0),
    }

    try:
        with open(iterations_log, "a") as f:
            f.write(json.dumps(iteration_entry) + "\n")
        print(f"✓ Metrics logged to ITERATIONS.jsonl")
        print(f"  paper@20: {metrics['paper@20']:.3f}")
        print(f"  theorem@20: {metrics['theorem@20']:.3f}")
        print(f"  avg_latency_s: {metrics['avg_latency_s']:.2f}")
        print(f"  mrr: {metrics.get('mrr', 0.0):.4f}")
    except Exception as e:
        print(f"Error: Failed to log metrics: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
