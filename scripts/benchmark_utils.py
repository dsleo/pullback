"""Utilities for benchmark evaluation: data models, I/O, and label alignment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from mathgent.discovery.arxiv.ids import normalize_arxiv_id
from mathgent.extraction.parsing import extract_environment_token
from mathgent.observability import get_logger

log = get_logger("eval_benchmark")


@dataclass
class BenchmarkItem:
    query: str
    gt_arxiv_id: str
    gt_theorem_label: str | None = None


@dataclass
class TheoremData:
    """Cached extracted theorems from a paper, for reranker evaluation."""
    query: str
    gt_arxiv_id: str
    theorems: list[dict]  # [{arxiv_id, line_number, header, snippet, score}, ...]


@dataclass
class DiscoveredPaperStatements:
    """All discovered papers and their statements for a query."""
    query: str
    discovered_count: int
    papers: list[dict]  # [{arxiv_id, header_count, headers: [{line_number, header, snippet}]}]


@dataclass
class BenchmarkResult:
    query: str
    gt_arxiv_id: str
    found: bool
    rank: int | None
    top_ids: list[str]
    latency_s: float
    paper_query: str | None = None
    statement_query: str | None = None
    discovery_queries: list[str] | None = None
    forager_query: str | None = None
    label_found: bool | None = None
    theorem_hit: bool | None = None
    theorem_rank: int | None = None
    theorem_score: float | None = None
    # per-phase timing
    discovery_time_s: float | None = None
    n_discovered: int | None = None
    worker_times_s: list[float] | None = None
    total_forager_time_s: float | None = None
    provider_timeouts: dict[str, int] | None = None
    # forager profiling breakdown
    plan_time_s: float | None = None
    execute_time_s: float | None = None
    fetch_time_s: float | None = None
    score_time_s: float | None = None


def iso_now() -> str:
    """Return current time as ISO 8601 string."""
    return datetime.utcnow().isoformat() + "Z"


def add_timestamp_to_path(path: Path) -> Path:
    """Insert date and time before file extension: foo.jsonl -> foo_2026-04-18_23-15.jsonl"""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    return path.parent / f"{path.stem}_{timestamp}{path.suffix}"


def load_items(path: Path, limit: int | None) -> list[BenchmarkItem]:
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
            label_raw = payload.get("gt_theorem_label")
            label = str(label_raw).strip() if label_raw is not None else None
            if not query or not gt:
                continue
            items.append(BenchmarkItem(query=query, gt_arxiv_id=gt, gt_theorem_label=label))
    return items


def result_payload(row: BenchmarkResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "query": row.query,
        "gt_arxiv_id": row.gt_arxiv_id,
        "found": row.found,
        "rank": row.rank,
        "top_ids": row.top_ids,
        "latency_s": round(row.latency_s, 4),
    }
    if row.paper_query is not None:
        payload["paper_query"] = row.paper_query
    if row.statement_query is not None:
        payload["statement_query"] = row.statement_query
    if row.discovery_queries is not None:
        payload["discovery_queries"] = row.discovery_queries
    if row.forager_query is not None:
        payload["forager_query"] = row.forager_query
    if row.label_found is not None:
        payload["label_found"] = row.label_found
    if row.theorem_hit is not None:
        payload["theorem_hit"] = row.theorem_hit
    if row.theorem_rank is not None:
        payload["theorem_rank"] = row.theorem_rank
    if row.theorem_score is not None:
        payload["theorem_score"] = round(float(row.theorem_score), 6)
    if row.discovery_time_s is not None:
        payload["discovery_time_s"] = row.discovery_time_s
    if row.n_discovered is not None:
        payload["n_discovered"] = row.n_discovered
    if row.worker_times_s is not None:
        payload["worker_times_s"] = row.worker_times_s
    if row.total_forager_time_s is not None:
        payload["total_forager_time_s"] = row.total_forager_time_s
    if row.provider_timeouts:
        payload["provider_timeouts"] = row.provider_timeouts
    if row.plan_time_s is not None:
        payload["plan_time_s"] = row.plan_time_s
    if row.execute_time_s is not None:
        payload["execute_time_s"] = row.execute_time_s
    if row.fetch_time_s is not None:
        payload["fetch_time_s"] = row.fetch_time_s
    if row.score_time_s is not None:
        payload["score_time_s"] = row.score_time_s
    return payload


def write_jsonl(path: Path, rows: Iterable[BenchmarkResult]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(result_payload(row), ensure_ascii=True) + "\n")


def append_jsonl(path: Path, row: BenchmarkResult) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result_payload(row), ensure_ascii=True) + "\n")
        handle.flush()


def save_discovered_statements(path: Path, data: DiscoveredPaperStatements) -> None:
    """Append discovered paper statements to JSONL file."""
    payload = {
        "query": data.query,
        "discovered_count": data.discovered_count,
        "papers": data.papers,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        handle.flush()


def result_key(query: str, gt_arxiv_id: str) -> tuple[str, str]:
    return (query.strip(), normalize_arxiv_id(gt_arxiv_id))


def load_existing_results(path: Path) -> tuple[dict[tuple[str, str], BenchmarkResult], list[BenchmarkResult]]:
    if not path.exists():
        return {}, []
    existing: dict[tuple[str, str], BenchmarkResult] = {}
    rows: list[BenchmarkResult] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            query = str(payload.get("query", "")).strip()
            gt_arxiv_id = str(payload.get("gt_arxiv_id", "")).strip()
            if not query or not gt_arxiv_id:
                continue
            row = BenchmarkResult(
                query=query,
                gt_arxiv_id=gt_arxiv_id,
                found=bool(payload.get("found")),
                rank=payload.get("rank"),
                top_ids=list(payload.get("top_ids") or []),
                latency_s=float(payload.get("latency_s") or 0.0),
                paper_query=payload.get("paper_query"),
                statement_query=payload.get("statement_query"),
                discovery_queries=payload.get("discovery_queries"),
                forager_query=payload.get("forager_query"),
                label_found=payload.get("label_found"),
                theorem_hit=payload.get("theorem_hit"),
                theorem_rank=payload.get("theorem_rank"),
                theorem_score=payload.get("theorem_score"),
            )
            key = result_key(query, gt_arxiv_id)
            if key in existing:
                continue
            existing[key] = row
            rows.append(row)
    return existing, rows


def normalize_label(label: str) -> str:
    cleaned = label.strip().rstrip(".")
    return " ".join(cleaned.split()).lower()


_ENV_LABEL_MAP: tuple[tuple[str, str], ...] = (
    ("theorem", "Theorem"),
    ("thm", "Theorem"),
    ("lemma", "Lemma"),
    ("lem", "Lemma"),
    ("proposition", "Proposition"),
    ("prop", "Proposition"),
    ("corollary", "Corollary"),
    ("cor", "Corollary"),
    ("claim", "Claim"),
)


def canonical_label_for_env(header_line: str) -> str | None:
    env = extract_environment_token(header_line)
    if not env:
        return None
    lower = env.lower()
    for keyword, label in _ENV_LABEL_MAP:
        if keyword in lower:
            return label
    return None


def align_labels_to_headers(headers, labels: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    if not headers or not labels:
        return mapping
    idx = 0
    total = len(labels)
    for header in headers:
        want = canonical_label_for_env(header.line)
        if not want:
            continue
        while idx < total:
            candidate = labels[idx]
            idx += 1
            parts = candidate.split(maxsplit=1)
            if not parts:
                continue
            prefix = parts[0]
            if prefix.lower() == want.lower():
                mapping[header.line_number] = candidate
                break
    return mapping


def load_theorem_cache(path: Path) -> dict[tuple[str, str], TheoremData]:
    """Load cached theorem data from JSONL file."""
    cache: dict[tuple[str, str], TheoremData] = {}
    if not path.exists():
        return cache
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                key = (data["query"], data["gt_arxiv_id"])
                cache[key] = TheoremData(
                    query=data["query"],
                    gt_arxiv_id=data["gt_arxiv_id"],
                    theorems=data.get("theorems", []),
                )
    except Exception as e:
        log.warning("failed to load theorem cache: {}", e)
    return cache


def save_theorem_data(path: Path, data: TheoremData) -> None:
    """Append theorem data to JSONL cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        payload = {
            "query": data.query,
            "gt_arxiv_id": data.gt_arxiv_id,
            "theorems": data.theorems,
        }
        f.write(json.dumps(payload) + "\n")


def summarize(results: list[BenchmarkResult]) -> None:
    total = len(results)
    mrr = sum((1.0 / r.rank) for r in results if r.rank) / total if total else 0.0
    avg_latency = sum(r.latency_s for r in results) / total if total else 0.0
    paper20_hits = sum(
        1
        for r in results
        if normalize_arxiv_id(r.gt_arxiv_id) in r.top_ids[:20]
    )
    theorem20_hits = sum(1 for r in results if r.theorem_hit)
    label_checked = [r for r in results if r.label_found is not None]
    label_hits = sum(1 for r in label_checked if r.label_found) if label_checked else 0
    print("\nSummary")
    print(f"queries: {total}")
    print(f"paper@20: {paper20_hits}/{total} ({(paper20_hits / total * 100.0):.1f}%)" if total else "paper@20: 0/0")
    print(f"theorem@20: {theorem20_hits}/{total} ({(theorem20_hits / total * 100.0):.1f}%)" if total else "theorem@20: 0/0")
    print(f"mrr: {mrr:.4f}")
    print(f"avg_latency_s: {avg_latency:.2f}")
    if label_checked:
        ratio = (label_hits / len(label_checked) * 100.0) if label_checked else 0.0
        print(f"label_found: {label_hits}/{len(label_checked)} ({ratio:.1f}%)")
    # provider timeout summary — show if any timeouts occurred
    all_timeouts: dict[str, int] = {}
    for r in results:
        for provider, count in (r.provider_timeouts or {}).items():
            all_timeouts[provider] = max(all_timeouts.get(provider, 0), count)
    if all_timeouts:
        timeout_str = "  ".join(f"{p}={c}" for p, c in sorted(all_timeouts.items()) if c > 0)
        if timeout_str:
            print(f"⚠ provider_timeouts (cumulative): {timeout_str}")
