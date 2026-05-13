#!/usr/bin/env python
"""
Enrich the theorem dataset with ground truth theorem information.

For each query, identifies which theorem in the candidate pool is the ground truth,
and marks it with is_gt=true. If the gt paper wasn't discovered, fetches and appends
the gt snippet separately.

Output: data/theorem_dataset_71_enriched.jsonl with additional fields:
  - theorems[*].is_gt: bool (at most one per query)
  - gt_label_found: bool (whether the gt label was findable in the paper)
  - gt_in_candidates: bool (whether gt paper was in discovered results)
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from pullback.api.deps import build_orchestrator
from pullback.observability import get_logger
from pullback.tools import ExtractionTools

# Load .env variables
load_dotenv(".env.local", override=False)

log = get_logger("enrich_theorem_dataset")


@dataclass
class BenchmarkItem:
    query: str
    gt_arxiv_id: str
    gt_theorem_label: str | None = None


@dataclass
class DatasetRecord:
    query: str
    gt_arxiv_id: str
    theorems: list[dict]
    gt_label_found: bool = False
    gt_in_candidates: bool = False


def _load_benchmark(path: Path) -> list[BenchmarkItem]:
    """Load benchmark items (with gt_theorem_label)."""
    items = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            query = payload.get("query", "").strip()
            gt_arxiv_id = payload.get("gt_arxiv_id", "").strip()
            gt_theorem_label = payload.get("gt_theorem_label")
            if query and gt_arxiv_id:
                items.append(
                    BenchmarkItem(query=query, gt_arxiv_id=gt_arxiv_id, gt_theorem_label=gt_theorem_label)
                )
    return items


def _load_dataset(path: Path) -> dict[str, DatasetRecord]:
    """Load theorem dataset and index by (query, gt_arxiv_id)."""
    records = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            query = payload.get("query", "")
            gt_arxiv_id = payload.get("gt_arxiv_id", "")
            theorems = payload.get("theorems", [])
            key = (query, gt_arxiv_id)
            records[key] = DatasetRecord(query=query, gt_arxiv_id=gt_arxiv_id, theorems=theorems)
    return records


def _normalize_label(label: str) -> str:
    """Normalize theorem label for matching (same as eval_benchmark.py)."""
    label = label.lower().strip()
    # Remove trailing punctuation
    while label and label[-1] in ".,:;!?":
        label = label[:-1]
    # Collapse whitespace
    label = " ".join(label.split())
    return label


def _align_labels_to_headers(headers, labels: list[str]) -> dict[int, str]:
    """
    Align ordered theorem labels to ordered headers by environment type.
    Same logic as eval_benchmark.py _align_labels_to_headers.
    """
    env_order = ["theorem", "lemma", "proposition", "corollary", "definition", "remark", "example"]

    def _get_env_type(header_line: str) -> str | None:
        header_lower = header_line.lower()
        for env in env_order:
            if f"\\begin{{{env}" in header_lower:
                return env
        return None

    # Group headers by environment type
    by_env: dict[str, list[int]] = {}
    for header in headers:
        env = _get_env_type(header.line)
        if env:
            by_env.setdefault(env, []).append(header.line_number)

    # Group labels by environment type
    label_by_env: dict[str, list[str]] = {}
    for label in labels:
        # Try to infer env from label text
        label_lower = label.lower()
        env = None
        for e in env_order:
            if e in label_lower:
                env = e
                break
        if env:
            label_by_env.setdefault(env, []).append(label)

    # Align: for each env, match i-th label to i-th header
    result = {}
    for env in env_order:
        env_headers = by_env.get(env, [])
        env_labels = label_by_env.get(env, [])
        for i, line_num in enumerate(env_headers):
            if i < len(env_labels):
                result[line_num] = env_labels[i]

    return result


async def _enrich_query(
    record: DatasetRecord,
    benchmark_item: BenchmarkItem,
    tools: ExtractionTools,
) -> DatasetRecord:
    """Enrich a single query record with gt theorem info."""

    if not benchmark_item.gt_theorem_label:
        return record

    gt_arxiv_id = benchmark_item.gt_arxiv_id
    gt_label = benchmark_item.gt_theorem_label

    try:
        # Get all labels and headers from gt paper
        labels = await tools.get_theorem_labels(gt_arxiv_id)
        headers = await tools.get_paper_headers(gt_arxiv_id)

        if not labels or not headers:
            log.warning("enrich_query query='{}' gt={} no_labels_or_headers", record.query[:50], gt_arxiv_id)
            return record

        # Normalize and find match
        normalized_gt = _normalize_label(gt_label)
        label_found = any(_normalize_label(lbl) == normalized_gt for lbl in labels)

        if not label_found:
            log.warning("enrich_query query='{}' gt={} label_not_found label={}", record.query[:50], gt_arxiv_id, gt_label)
            return record

        record.gt_label_found = True

        # Align labels to headers
        alignment = _align_labels_to_headers(headers, labels)

        # Find gt header's line number
        gt_line_number = None
        for line_num, aligned_label in alignment.items():
            if _normalize_label(aligned_label) == normalized_gt:
                gt_line_number = line_num
                break

        if gt_line_number is None:
            log.warning("enrich_query query='{}' gt={} alignment_failed", record.query[:50], gt_arxiv_id)
            return record

        # Check if gt paper is in candidates
        gt_in_candidates = any(t["arxiv_id"] == gt_arxiv_id for t in record.theorems)

        if gt_in_candidates:
            # Mark the matching theorem as is_gt=true
            for theorem in record.theorems:
                if theorem["arxiv_id"] == gt_arxiv_id and theorem["line_number"] == gt_line_number:
                    theorem["is_gt"] = True
                    record.gt_in_candidates = True
                    log.info("enrich_query query='{}' gt={} found_in_candidates line={}", record.query[:50], gt_arxiv_id, gt_line_number)
                    break
        else:
            # Fetch gt snippet and append
            gt_header = next((h for h in headers if h.line_number == gt_line_number), None)
            if gt_header:
                try:
                    snippet = await tools.fetch_header_block(
                        gt_arxiv_id,
                        gt_line_number,
                        gt_header.line,
                        context_lines=20,
                    )
                    record.theorems.append(
                        {
                            "arxiv_id": gt_arxiv_id,
                            "line_number": gt_line_number,
                            "header": gt_header.line,
                            "snippet": snippet,
                            "score": 0.0,  # Will be reranked
                            "is_gt": True,
                        }
                    )
                    record.gt_in_candidates = True
                    log.info("enrich_query query='{}' gt={} appended_gt_snippet line={}", record.query[:50], gt_arxiv_id, gt_line_number)
                except Exception as e:
                    log.error("enrich_query query='{}' gt={} fetch_failed error={}", record.query[:50], gt_arxiv_id, e)

    except Exception as e:
        log.error("enrich_query query='{}' error={}", record.query[:50], e)

    return record


async def _enrich_dataset(
    dataset_path: Path,
    benchmark_path: Path,
    output_path: Path,
) -> None:
    """Enrich the entire dataset."""
    log.info("Loading dataset from {}", dataset_path)
    dataset = _load_dataset(dataset_path)
    log.info("Loaded {} records", len(dataset))

    log.info("Loading benchmark from {}", benchmark_path)
    benchmark = _load_benchmark(benchmark_path)
    log.info("Loaded {} benchmark items", len(benchmark))

    # Build benchmark index
    benchmark_index = {(item.query, item.gt_arxiv_id): item for item in benchmark}

    orchestrator = build_orchestrator()
    try:
        with output_path.open("w") as out:
            for idx, (key, record) in enumerate(dataset.items(), start=1):
                query, gt_arxiv_id = key
                benchmark_item = benchmark_index.get(key)

                if not benchmark_item:
                    # Write as-is if no benchmark match
                    out.write(json.dumps(_to_dict(record)) + "\n")
                    continue

                # Enrich
                enriched = await _enrich_query(record, benchmark_item, orchestrator.tools)

                out.write(json.dumps(_to_dict(enriched)) + "\n")

                gt_status = "✓" if enriched.gt_in_candidates else "✗"
                label_found = "yes" if enriched.gt_label_found else "no"
                print(f"[{idx}/{len(dataset)}] {gt_status} {query[:50]:50} label_found={label_found}")

    finally:
        orchestrator.close()

    log.info("Wrote enriched dataset to {}", output_path)


def _to_dict(record: DatasetRecord) -> dict:
    """Convert to JSON-serializable dict."""
    return {
        "query": record.query,
        "gt_arxiv_id": record.gt_arxiv_id,
        "theorems": record.theorems,
        "gt_label_found": record.gt_label_found,
        "gt_in_candidates": record.gt_in_candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich theorem dataset with ground truth information")
    parser.add_argument("--dataset", type=Path, default=Path("data/theorem_dataset_71.jsonl"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/benchmark_clean_71.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/theorem_dataset_71_enriched.jsonl"))

    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"Error: dataset not found: {args.dataset}", file=sys.stderr)
        sys.exit(1)

    if not args.benchmark.exists():
        print(f"Error: benchmark not found: {args.benchmark}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(_enrich_dataset(args.dataset, args.benchmark, args.output))


if __name__ == "__main__":
    main()
