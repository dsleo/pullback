#!/usr/bin/env python
"""Analyze and compare iterations from ITERATIONS.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_iterations() -> list[dict[str, Any]]:
    """Load all iterations from ITERATIONS.jsonl."""
    iterations_log = ROOT / "configs" / "ITERATIONS.jsonl"

    if not iterations_log.exists():
        print(f"Error: {iterations_log} not found", file=sys.stderr)
        sys.exit(1)

    iterations = []
    with open(iterations_log) as f:
        for line in f:
            if line.strip():
                iterations.append(json.loads(line))

    return sorted(iterations, key=lambda x: x["iteration"])


def cmd_list(args) -> None:
    """List all iterations with key metrics."""
    iterations = load_iterations()

    if not iterations:
        print("No iterations found.")
        return

    # Print header
    print(f"{'Iter':<4} {'Config':<25} {'Paper@20':<10} {'Theorem@20':<12} {'Latency':<10} {'Status':<8}")
    print("-" * 80)

    for it in iterations:
        paper = f"{it['paper@20']:.3f}"
        theorem = f"{it['theorem@20']:.3f}"
        latency = f"{it['avg_latency_s']:.2f}s"
        status = it["status"]
        config_name = it["config_name"][:24]

        print(f"{it['iteration']:<4} {config_name:<25} {paper:<10} {theorem:<12} {latency:<10} {status:<8}")


def cmd_best(args) -> None:
    """Show best iteration for a given metric."""
    iterations = load_iterations()

    if not iterations:
        print("No iterations found.")
        return

    metric = args.metric
    if metric not in ["paper@20", "theorem@20", "avg_latency_s", "mrr"]:
        print(f"Error: unknown metric '{metric}'", file=sys.stderr)
        sys.exit(1)

    # Filter by status if requested
    if args.status:
        iterations = [it for it in iterations if it["status"] == args.status]

    if not iterations:
        print(f"No iterations with status '{args.status}'")
        return

    # Find best
    if metric == "avg_latency_s":
        best = min(iterations, key=lambda x: x[metric])
    else:
        best = max(iterations, key=lambda x: x[metric])

    print(f"Best by {metric}:")
    print(f"  Iteration: {best['iteration']}")
    print(f"  Config: {best['config_name']}")
    print(f"  {metric}: {best[metric]:.4f}")
    print(f"  Hypothesis: {best['hypothesis']}")
    print(f"  Status: {best['status']}")
    print(f"  Config file: {best['config_file']}")


def cmd_compare(args) -> None:
    """Compare two iterations."""
    iterations = load_iterations()

    try:
        iter1 = next(it for it in iterations if it["iteration"] == args.iter1)
        iter2 = next(it for it in iterations if it["iteration"] == args.iter2)
    except StopIteration:
        print(f"Error: iteration not found", file=sys.stderr)
        sys.exit(1)

    metrics = ["paper@20", "theorem@20", "avg_latency_s", "mrr"]

    print(f"Comparison: Iteration {args.iter1} vs {args.iter2}")
    print(f"{'Metric':<20} {'Iter {}':<15} {'Iter {}':<15} {'Delta':<10}")
    print(f"{'':20} {args.iter1:<15} {args.iter2:<15} {'':10}")
    print("-" * 70)

    for metric in metrics:
        v1 = iter1.get(metric, 0)
        v2 = iter2.get(metric, 0)

        if metric == "avg_latency_s":
            delta = v2 - v1
            delta_str = f"{delta:+.2f}s" if delta != 0 else "0.00s"
            print(f"{metric:<20} {v1:<15.2f} {v2:<15.2f} {delta_str:<10}")
        else:
            delta = v2 - v1
            delta_pct = (delta / v1 * 100) if v1 != 0 else 0
            delta_str = f"{delta:+.4f} ({delta_pct:+.1f}%)"
            print(f"{metric:<20} {v1:<15.4f} {v2:<15.4f} {delta_str:<10}")


def cmd_diff_config(args) -> None:
    """Show diff between two config files."""
    config1_path = ROOT / args.config1
    config2_path = ROOT / args.config2

    if not config1_path.exists() or not config2_path.exists():
        print(f"Error: config file not found", file=sys.stderr)
        sys.exit(1)

    with open(config1_path) as f:
        config1 = json.load(f)
    with open(config2_path) as f:
        config2 = json.load(f)

    def flatten_dict(d, parent_key=""):
        """Flatten nested dict to dot-notation keys."""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}.{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(flatten_dict(v, new_key).items())
            else:
                items.append((new_key, v))
        return dict(items)

    flat1 = flatten_dict(config1)
    flat2 = flatten_dict(config2)

    all_keys = set(flat1.keys()) | set(flat2.keys())
    changes = []

    for key in sorted(all_keys):
        v1 = flat1.get(key)
        v2 = flat2.get(key)
        if v1 != v2:
            changes.append((key, v1, v2))

    if not changes:
        print("No differences found.")
        return

    print(f"Differences between configs:")
    print(f"{'Key':<40} {'Before':<20} {'After':<20}")
    print("-" * 80)

    for key, v1, v2 in changes:
        print(f"{key:<40} {str(v1):<20} {str(v2):<20}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze optimization iterations")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    subparsers.add_parser("list", help="List all iterations")

    # best command
    best_parser = subparsers.add_parser("best", help="Show best iteration for a metric")
    best_parser.add_argument("metric", choices=["paper@20", "theorem@20", "avg_latency_s", "mrr"],
                             help="Metric to optimize for")
    best_parser.add_argument("--status", choices=["ACCEPT", "REVERT", "PENDING"],
                             help="Filter by status")

    # compare command
    compare_parser = subparsers.add_parser("compare", help="Compare two iterations")
    compare_parser.add_argument("iter1", type=int, help="First iteration number")
    compare_parser.add_argument("iter2", type=int, help="Second iteration number")

    # diff-config command
    diff_parser = subparsers.add_parser("diff-config", help="Show config differences")
    diff_parser.add_argument("config1", help="First config file path")
    diff_parser.add_argument("config2", help="Second config file path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "list":
            cmd_list(args)
        elif args.command == "best":
            cmd_best(args)
        elif args.command == "compare":
            cmd_compare(args)
        elif args.command == "diff-config":
            cmd_diff_config(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
