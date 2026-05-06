#!/usr/bin/env python
"""Minimal benchmark runner for the benchmark datasets."""

from __future__ import annotations

# Patch e2b/httpx compatibility issue
try:
    import httpx._types as _httpx_types
    if not hasattr(_httpx_types, 'ProxyTypes'):
        _httpx_types.ProxyTypes = _httpx_types.ProxiesTypes
except Exception:
    pass

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mathgent.api.deps import build_orchestrator  # noqa: E402
from mathgent.config import get_config  # noqa: E402
from mathgent.discovery.arxiv.ids import normalize_arxiv_id  # noqa: E402
from mathgent.extraction.parsing import extract_environment_token  # noqa: E402
from mathgent.rerank.factory import create_reranker  # noqa: E402
from mathgent.settings import load_settings  # noqa: E402
from mathgent.observability import get_logger  # noqa: E402

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


def _iso_now() -> str:
    """Return current time as ISO 8601 string."""
    return datetime.utcnow().isoformat() + "Z"


def _add_timestamp_to_path(path: Path) -> Path:
    """Insert date and time before file extension: foo.jsonl -> foo_2026-04-18_23-15.jsonl"""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    return path.parent / f"{path.stem}_{timestamp}{path.suffix}"


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
            label_raw = payload.get("gt_theorem_label")
            label = str(label_raw).strip() if label_raw is not None else None
            if not query or not gt:
                continue
            items.append(BenchmarkItem(query=query, gt_arxiv_id=gt, gt_theorem_label=label))
    return items


def _result_payload(row: BenchmarkResult) -> dict[str, object]:
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


def _write_jsonl(path: Path, rows: Iterable[BenchmarkResult]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_result_payload(row), ensure_ascii=True) + "\n")


def _append_jsonl(path: Path, row: BenchmarkResult) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_result_payload(row), ensure_ascii=True) + "\n")
        handle.flush()


def _save_discovered_statements(path: Path, data: DiscoveredPaperStatements) -> None:
    """Append discovered paper statements to JSONL file."""
    payload = {
        "query": data.query,
        "discovered_count": data.discovered_count,
        "papers": data.papers,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        handle.flush()


def _result_key(query: str, gt_arxiv_id: str) -> tuple[str, str]:
    return (query.strip(), normalize_arxiv_id(gt_arxiv_id))


def _load_existing_results(path: Path) -> tuple[dict[tuple[str, str], BenchmarkResult], list[BenchmarkResult]]:
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
            key = _result_key(query, gt_arxiv_id)
            if key in existing:
                continue
            existing[key] = row
            rows.append(row)
    return existing, rows


def _normalize_label(label: str) -> str:
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


def _canonical_label_for_env(header_line: str) -> str | None:
    env = extract_environment_token(header_line)
    if not env:
        return None
    lower = env.lower()
    for keyword, label in _ENV_LABEL_MAP:
        if keyword in lower:
            return label
    return None


def _align_labels_to_headers(headers, labels: list[str]) -> dict[int, str]:
    """Align theorem labels to headers by environment type.

    Groups labels by canonical type (Theorem, Lemma, etc.) and aligns them
    positionally within each type group in paper order. This correctly handles
    pre-seeded label lists that are not in paper order (e.g. when trust_gt_labels
    seeds labels from a dataset that lists them in a different order than they
    appear in the paper), unlike the previous greedy sequential scan which would
    consume labels of the wrong type and leave later headers unmatched.
    """
    import re as _re
    from collections import defaultdict as _defaultdict

    mapping: dict[int, str] = {}
    if not headers or not labels:
        return mapping

    def _numeric_sort_key(label: str) -> tuple[int, ...]:
        return tuple(int(n) for n in _re.findall(r"\d+", label))

    # Group labels by canonical type, sorted numerically within each group
    labels_by_type: dict[str, list[str]] = _defaultdict(list)
    for label in labels:
        parts = label.split(maxsplit=1)
        if not parts:
            continue
        prefix = parts[0].lower()
        for keyword, canonical in _ENV_LABEL_MAP:
            if keyword == prefix:
                labels_by_type[canonical].append(label)
                break
    for canonical in labels_by_type:
        labels_by_type[canonical].sort(key=_numeric_sort_key)

    # Walk headers in paper order; for each type assign labels in sequence
    type_idx: dict[str, int] = _defaultdict(int)
    for header in headers:
        want = _canonical_label_for_env(header.line)
        if not want:
            continue
        available = labels_by_type.get(want, [])
        pos = type_idx[want]
        if pos < len(available):
            mapping[header.line_number] = available[pos]
            type_idx[want] += 1

    return mapping


def _load_theorem_cache(path: Path) -> dict[tuple[str, str], TheoremData]:
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


def _save_theorem_data(path: Path, data: TheoremData) -> None:
    """Append theorem data to JSONL cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        payload = {
            "query": data.query,
            "gt_arxiv_id": data.gt_arxiv_id,
            "theorems": data.theorems,
        }
        f.write(json.dumps(payload) + "\n")


async def _run_benchmark(
    items: list[BenchmarkItem],
    *,
    max_results: int,
    strictness: float,
    validate_labels: bool,
    output_path: Path | None = None,
    theorem_cache_path: Path | None = None,
    force_fresh: bool = False,
    statements_path: Path | None = None,
    trust_gt_labels: bool = False,
) -> list[BenchmarkResult]:
    orchestrator = build_orchestrator()
    settings = load_settings()
    reranker = create_reranker(
        settings.rerank.strategy,
        colbert_endpoint=settings.rerank.colbert_endpoint,
        bge_model=settings.rerank.bge_model,
        openrouter_model=settings.rerank.openrouter_model,
        api_key=settings.rerank.api_key,
    )
    results: list[BenchmarkResult] = []
    label_cache: dict[str, list[str]] = {}
    # Pre-seed label_cache with GT labels when caller guarantees they're valid.
    # This bypasses E2B get_theorem_labels (which often fails with 403 on old math/ papers)
    # and sets label_found=True so theorem scoring can proceed when the paper IS found.
    if trust_gt_labels:
        for item in items:
            pid = normalize_arxiv_id(item.gt_arxiv_id)
            if item.gt_theorem_label:
                label_cache.setdefault(pid, []).append(item.gt_theorem_label)
    header_cache: dict[str, list] = {}
    label_map_cache: dict[str, dict[int, str]] = {}
    snippet_cache: dict[str, dict[int, str]] = {}

    # Load theorem cache for faster reranker iteration
    theorem_cache: dict[tuple[str, str], TheoremData] = {}
    if theorem_cache_path and not force_fresh:
        theorem_cache = _load_theorem_cache(theorem_cache_path)
        if theorem_cache:
            log.info("loaded theorem cache with {} entries", len(theorem_cache))

    # per-query phase timing collected via hooks
    _col: dict = {}

    def _on_disc_start(**kw):
        _col["disc_start"] = time.perf_counter()

    def _on_disc_done(**kw):
        if "disc_start" in _col:
            _col["disc_time_s"] = round(time.perf_counter() - _col["disc_start"], 4)
        _col["n_discovered"] = (_col.get("n_discovered") or 0) + len(kw.get("arxiv_ids") or [])
        # snapshot cumulative provider timeout counts at this point in the query
        for provider, count in (kw.get("provider_timeouts") or {}).items():
            _col.setdefault("provider_timeouts", {})[provider] = count

    def _on_worker_done(state, **kw):
        if state.started_at and state.finished_at:
            worker_time = round(state.finished_at - state.started_at, 4)
            _col.setdefault("workers", []).append(worker_time)

    def _on_plan_complete(**kw):
        plan_time = kw.get("plan_time_s")
        if plan_time is not None:
            _col["plan_time_s"] = plan_time

    def _on_execute_complete(**kw):
        exec_time = kw.get("execute_time_s")
        fetch_time = kw.get("fetch_time_s")
        score_time = kw.get("score_time_s")
        if exec_time is not None:
            _col["execute_time_s"] = exec_time
        if fetch_time is not None:
            _col["fetch_time_s"] = fetch_time
        if score_time is not None:
            _col["score_time_s"] = score_time

    orchestrator.on("discovery_start", _on_disc_start)
    orchestrator.on("discovery_done", _on_disc_done)
    orchestrator.on("worker_done", _on_worker_done)
    # Forager profiling hooks
    orchestrator.forager.on("plan_complete", _on_plan_complete)
    orchestrator.forager.on("execute_complete", _on_execute_complete)

    try:
        total = len(items)
        for idx, item in enumerate(items, start=1):
            log.info("benchmark.query_start [{}/{}] query={!r}", idx, total, item.query[:100])
            _col.clear()
            start = time.perf_counter()
            try:
                response = await orchestrator.search(
                    query=item.query,
                    max_results=max_results,
                    strictness=strictness,
                )
            except Exception as query_exc:
                latency = time.perf_counter() - start
                log.error("benchmark.query_error [{}/{}] {}: {}", idx, total, type(query_exc).__name__, query_exc)
                err_result = BenchmarkResult(
                    query=item.query,
                    gt_arxiv_id=item.gt_arxiv_id,
                    found=False,
                    rank=None,
                    top_ids=[],
                    latency_s=latency,
                    paper_query=None,
                    statement_query=None,
                    discovery_queries=None,
                    forager_query=None,
                    label_found=None,
                    theorem_hit=None,
                    theorem_rank=None,
                    theorem_score=None,
                    discovery_time_s=None,
                    n_discovered=None,
                    worker_times_s=None,
                    total_forager_time_s=None,
                    provider_timeouts=None,
                    plan_time_s=None,
                    execute_time_s=None,
                    fetch_time_s=None,
                    score_time_s=None,
                )
                results.append(err_result)
                if output_path is not None:
                    _append_jsonl(output_path, err_result)
                print(f"[{idx}/{len(items)}] ERROR {type(query_exc).__name__}: {str(query_exc)[:80]}")
                continue
            latency = time.perf_counter() - start
            paper_query = getattr(response, "paper_query", None)
            statement_query = getattr(response, "statement_query", None)
            discovery_queries = getattr(response, "discovery_queries", None)
            forager_query = getattr(response, "forager_query", None)
            target = normalize_arxiv_id(item.gt_arxiv_id)
            top_ids = [normalize_arxiv_id(entry.arxiv_id) for entry in response.results]
            rank = None
            for pos, arxiv_id in enumerate(top_ids, start=1):
                if arxiv_id == target:
                    rank = pos
                    break
            paper_in_top20 = target in top_ids[:20]
            label_found: bool | None = None
            theorem_hit: bool | None = None
            theorem_rank: int | None = None
            theorem_score: float | None = None
            if validate_labels and item.gt_theorem_label:
                tools = getattr(orchestrator, "tools", None)
                if tools is None:
                    raise RuntimeError("orchestrator.tools not available for label validation")
                paper_id = normalize_arxiv_id(item.gt_arxiv_id)
                if paper_id not in label_cache:
                    try:
                        label_cache[paper_id] = await tools.get_theorem_labels(paper_id)
                    except Exception as label_exc:
                        log.warning("get_theorem_labels failed for {}: {}", paper_id, label_exc)
                        label_cache[paper_id] = []
                normalized = _normalize_label(item.gt_theorem_label)
                label_found = any(_normalize_label(lbl) == normalized for lbl in label_cache[paper_id])
                theorem_hit = False

                if label_found:
                    # Check if theorems are cached
                    cache_key = (item.query, paper_id)
                    if cache_key in theorem_cache:
                        # Use cached theorems
                        cached_theorems = theorem_cache[cache_key].theorems
                        log.info("using {} cached theorems for query={!r}", len(cached_theorems), item.query[:50])
                        scored = []
                        for th in cached_theorems:
                            snippet = th.get("snippet", "")
                            score = float(reranker.score(item.query, snippet))
                            label = th.get("label")
                            is_gt = bool(label and _normalize_label(label) == normalized)
                            if is_gt:
                                theorem_score = score
                            scored.append((score, is_gt))
                    else:
                        try:
                            # Extract theorems and cache them
                            if paper_id not in header_cache:
                                header_cache[paper_id] = await tools.get_paper_headers(paper_id)
                            if paper_id not in label_map_cache:
                                label_map_cache[paper_id] = _align_labels_to_headers(
                                    header_cache[paper_id],
                                    label_cache[paper_id],
                                )
                            if paper_id not in snippet_cache:
                                fetch_bulk = getattr(tools, "fetch_header_blocks", None)
                                if callable(fetch_bulk):
                                    snippet_cache[paper_id] = await fetch_bulk(
                                        paper_id,
                                        header_cache[paper_id],
                                        context_lines=20,
                                    )
                                else:
                                    snippet_cache[paper_id] = {}

                            # Collect theorem data for caching
                            extracted_theorems = []
                            scored = []
                            for header in header_cache[paper_id]:
                                snippet = snippet_cache[paper_id].get(header.line_number, "")
                                if not snippet:
                                    snippet = await tools.fetch_header_block(
                                        paper_id,
                                        header.line_number,
                                        header.line,
                                        context_lines=20,
                                    )
                                score = float(reranker.score(item.query, snippet))
                                label = label_map_cache[paper_id].get(header.line_number)
                                is_gt = bool(label and _normalize_label(label) == normalized)
                                if is_gt:
                                    theorem_score = score

                                # Save theorem data for cache
                                extracted_theorems.append({
                                    "arxiv_id": paper_id,
                                    "line_number": header.line_number,
                                    "header": header.line,
                                    "snippet": snippet,
                                    "label": label,
                                })
                                scored.append((score, is_gt))

                            # Save to theorem cache
                            if theorem_cache_path and extracted_theorems:
                                theorem_data = TheoremData(
                                    query=item.query,
                                    gt_arxiv_id=paper_id,
                                    theorems=extracted_theorems,
                                )
                                _save_theorem_data(theorem_cache_path, theorem_data)
                                theorem_cache[cache_key] = theorem_data
                        except Exception as extract_exc:
                            log.warning(
                                "theorem extraction failed for {} ({}): {}",
                                paper_id, type(extract_exc).__name__, extract_exc,
                            )
                            scored = []

                    # Fallback: if alignment produced no is_gt match, try to identify
                    # the GT header by keyword overlap between the GT label and the
                    # header's optional argument text (e.g. \begin{corollary}[Rigidity...]).
                    # This handles papers whose theorem environments use custom names or
                    # non-standard counters that defeat positional label alignment.
                    if scored and not any(is_gt for _, is_gt in scored) and item.gt_theorem_label:
                        gt_words = {
                            w.lower() for w in item.gt_theorem_label.split()
                            if len(w) > 3 and not w[0].isdigit()
                        }
                        best_overlap, best_idx = 0, -1
                        for j, th in enumerate(extracted_theorems):
                            header_text = th.get("header", "").lower()
                            overlap = sum(1 for w in gt_words if w in header_text)
                            if overlap > best_overlap:
                                best_overlap, best_idx = overlap, j
                        if best_idx >= 0 and best_overlap >= 1:
                            s, _ = scored[best_idx]
                            scored[best_idx] = (s, True)
                            theorem_score = s
                            log.info(
                                "label_align_fallback: matched GT label {!r} to header {!r} via keyword overlap={}",
                                item.gt_theorem_label,
                                extracted_theorems[best_idx].get("header", "")[:60],
                                best_overlap,
                            )

                    # Score and rank theorems
                    scored.sort(key=lambda x: x[0], reverse=True)
                    for i, (_, is_gt) in enumerate(scored, start=1):
                        if is_gt:
                            theorem_rank = i
                            break
                    theorem_hit = bool(paper_in_top20 and theorem_rank is not None and theorem_rank <= 20)

            # Extract statements from all discovered papers for reranker replay analysis
            if statements_path and top_ids:
                tools = getattr(orchestrator, "tools", None)
                discovered_papers = []
                for paper_id in top_ids:
                    try:
                        if paper_id not in header_cache:
                            header_cache[paper_id] = await tools.get_paper_headers(paper_id)
                        if paper_id not in snippet_cache:
                            fetch_bulk = getattr(tools, "fetch_header_blocks", None)
                            if callable(fetch_bulk):
                                snippet_cache[paper_id] = await fetch_bulk(
                                    paper_id,
                                    header_cache[paper_id],
                                    context_lines=20,
                                )
                            else:
                                snippet_cache[paper_id] = {}

                        # Collect statements for this paper
                        headers_with_snippets = []
                        for header in header_cache[paper_id]:
                            snippet = snippet_cache[paper_id].get(header.line_number, "")
                            if not snippet and callable(getattr(tools, "fetch_header_block", None)):
                                snippet = await tools.fetch_header_block(
                                    paper_id,
                                    header.line_number,
                                    header.line,
                                    context_lines=20,
                                )
                            headers_with_snippets.append({
                                "line_number": header.line_number,
                                "header": header.line,
                                "snippet": snippet,
                            })

                        if headers_with_snippets:
                            discovered_papers.append({
                                "arxiv_id": paper_id,
                                "header_count": len(header_cache[paper_id]),
                                "headers": headers_with_snippets,
                            })
                    except Exception as e:
                        log.warning("failed to extract statements for {}: {}", paper_id, e)

                if discovered_papers:
                    statements_data = DiscoveredPaperStatements(
                        query=item.query,
                        discovered_count=len(top_ids),
                        papers=discovered_papers,
                    )
                    _save_discovered_statements(statements_path, statements_data)

            _workers = _col.get("workers", [])
            results.append(
                BenchmarkResult(
                    query=item.query,
                    gt_arxiv_id=item.gt_arxiv_id,
                    found=rank is not None,
                    rank=rank,
                    top_ids=top_ids,
                    latency_s=latency,
                    paper_query=paper_query,
                    statement_query=statement_query,
                    discovery_queries=discovery_queries,
                    forager_query=forager_query,
                    label_found=label_found,
                    theorem_hit=theorem_hit,
                    theorem_rank=theorem_rank,
                    theorem_score=theorem_score,
                    discovery_time_s=_col.get("disc_time_s"),
                    n_discovered=_col.get("n_discovered"),
                    worker_times_s=_workers or None,
                    total_forager_time_s=round(sum(_workers), 4) if _workers else None,
                    provider_timeouts=_col.get("provider_timeouts") or None,
                    plan_time_s=_col.get("plan_time_s"),
                    execute_time_s=_col.get("execute_time_s"),
                    fetch_time_s=_col.get("fetch_time_s"),
                    score_time_s=_col.get("score_time_s"),
                )
            )
            if output_path is not None:
                _append_jsonl(output_path, results[-1])
            status = "hit" if rank is not None else "miss"
            disc_s = _col.get("disc_time_s")
            n_disc = _col.get("n_discovered", 0)
            forager_s = round(sum(_col.get("workers", [])), 2) if _col.get("workers") else None
            disc_str = f" disc={disc_s:.2f}s({n_disc}p)" if disc_s is not None else ""
            forager_str = f" forager={forager_s:.2f}s" if forager_s is not None else ""
            print(f"[{idx}/{len(items)}] {status} rank={rank or '-'} latency={latency:.2f}s{disc_str}{forager_str}")
    finally:
        orchestrator.close()

    return results


def _summarize(results: list[BenchmarkResult]) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Mathgent benchmark on a JSONL dataset.")
    parser.add_argument(
        "--data",
        default=str(ROOT / "data" / "benchmark_clean_71.jsonl"),
        help="Path to benchmark JSONL (default: data/benchmark_clean_71.jsonl).",
    )
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--strictness", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=str, default=None, help="Optional JSONL output path.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing output JSONL and theorem cache (skip completed queries).",
    )
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Ignore cached theorems and recompute from scratch (use with --output).",
    )
    parser.add_argument(
        "--validate-labels",
        action="store_true",
        help="Check gt_theorem_label presence in the ground-truth paper.",
    )
    parser.add_argument(
        "--trust-gt-labels",
        action="store_true",
        help=(
            "Pre-seed label cache with GT labels from the input file. "
            "Use when labels are pre-validated (e.g. via validate_remaining_39.py) "
            "and E2B sandbox downloads would fail (403/404 on old arXiv papers)."
        ),
    )
    args = parser.parse_args()

    data_path = Path(args.data).expanduser().resolve()
    if not data_path.exists():
        raise SystemExit(f"data file not found: {data_path}")

    items = _load_items(data_path, args.limit)
    if not items:
        raise SystemExit("no benchmark items found")

    output_path = Path(args.output).expanduser().resolve() if args.output else None

    # Add timestamp to output filename if provided
    if output_path:
        output_path = _add_timestamp_to_path(output_path)

    # Derive theorem cache and statements paths from output path (with timestamp)
    theorem_cache_path: Path | None = None
    statements_path: Path | None = None
    if output_path:
        theorem_cache_path = output_path.parent / f"{output_path.stem}_theorems.jsonl"
        statements_path = output_path.parent / f"{output_path.stem}_statements.jsonl"

    existing_map: dict[tuple[str, str], BenchmarkResult] = {}
    existing_rows: list[BenchmarkResult] = []
    if output_path and args.resume:
        existing_map, existing_rows = _load_existing_results(output_path)
    if output_path and not args.resume:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        # Clear caches if starting fresh
        if theorem_cache_path and theorem_cache_path.exists():
            theorem_cache_path.unlink()
        if statements_path and statements_path.exists():
            statements_path.unlink()
    elif output_path and args.resume:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.touch(exist_ok=True)

    if args.resume and existing_map:
        remaining = [item for item in items if _result_key(item.query, item.gt_arxiv_id) not in existing_map]
    else:
        remaining = items

    if remaining:
        results = asyncio.run(
            _run_benchmark(
                remaining,
                max_results=max(1, args.max_results),
                strictness=min(max(args.strictness, 0.0), 1.0),
                validate_labels=args.validate_labels,
                output_path=output_path,
                theorem_cache_path=theorem_cache_path,
                force_fresh=args.force_fresh,
                statements_path=statements_path,
                trust_gt_labels=args.trust_gt_labels,
            )
        )
    else:
        results = []
    all_results = existing_rows + results
    _summarize(all_results)

    if output_path and not args.resume:
        _write_jsonl(output_path, results)
        print(f"wrote: {output_path}")
    if theorem_cache_path and theorem_cache_path.exists():
        print(f"cached theorems: {theorem_cache_path}")
    if statements_path and statements_path.exists():
        print(f"cached statements: {statements_path}")


if __name__ == "__main__":
    main()
