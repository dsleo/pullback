"""SSE stream generator for The Pullback demo."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator


def _search_stream(
    query: str,
    max_results: int,
    strictness: float,
    build_orchestrator,
) -> AsyncGenerator[str, None]:
    return _run_stream(query, max_results, strictness, build_orchestrator)


async def _run_stream(
    query: str,
    max_results: int,
    strictness: float,
    build_orchestrator,
) -> AsyncGenerator[str, None]:
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def push(payload: dict) -> None:
        await queue.put(payload)

    orch = build_orchestrator()
    forager = orch.forager

    # ── intercept _query_attempts to emit queries_planned before discovery ──
    _orig_query_attempts = orch._query_attempts

    async def _patched_query_attempts(q: str) -> list[str]:
        attempts = await _orig_query_attempts(q)
        ordered_attempts: list[str] = []
        seen: set[str] = set()
        for candidate in [q, *attempts]:
            key = " ".join(candidate.lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            ordered_attempts.append(candidate)
        await push({"type": "queries_planned", "queries": ordered_attempts})
        return ordered_attempts

    orch._query_attempts = _patched_query_attempts  # type: ignore[method-assign]

    # ── librarian hooks ──────────────────────────────────────────────────────

    async def on_search_start(*, query, max_results, **_):
        await push({"type": "query_start", "query": query,
                    "max_results": max_results, "strictness": strictness})

    # Track metadata fetches — awaited before the stream sentinel
    fetched_metadata_ids: set[str] = set()
    _metadata_tasks: list[asyncio.Task] = []

    def _paper_payload(aid: str, metadata) -> dict | None:
        if not getattr(metadata, "title", None):
            return None
        return {
            "arxiv_id": aid,
            "title": metadata.title,
            "authors": list(metadata.authors or []),
            "year": metadata.year,
            "cited_by_count": metadata.cited_by_count,
        }

    async def _fetch_and_push_metadata(ids: list[str], *, query: str) -> None:
        if orch._metadata_fetcher is None:
            return
        new_ids = [aid for aid in ids if aid not in fetched_metadata_ids]
        if not new_ids:
            return
        try:
            meta = await orch._metadata_fetcher(new_ids)
            if meta:
                papers = [
                    payload
                    for aid, m in meta.items()
                    for payload in [_paper_payload(aid, m)]
                    if payload is not None
                ]
                if papers:
                    # Only mark as fetched if we successfully got metadata for them
                    fetched_metadata_ids.update(p["arxiv_id"] for p in papers)
                    await push({"type": "metadata_update", "query": query, "papers": papers})
        except Exception:
            pass  # metadata is best-effort

    async def on_discovery_done(*, query, arxiv_ids, metadata=None, **_):
        ids = list(arxiv_ids)
        papers_by_id: dict[str, dict] = {}

        # Use metadata the provider already fetched (e.g. OpenAlex returns titles inline).
        if metadata:
            for aid, m in metadata.items():
                payload = _paper_payload(aid, m)
                if payload is None:
                    continue
                papers_by_id[aid] = payload
                # Only mark as complete when we have the full visible tuple.
                if m.title and m.authors and m.year:
                    fetched_metadata_ids.add(aid)

        # Kick off arXiv metadata in parallel (best-effort). The UI should not
        # block on this; it will update cards as metadata arrives.
        needs_fetch = [
            aid
            for aid in ids
            if aid not in fetched_metadata_ids
            and (
                aid not in papers_by_id
                or not papers_by_id[aid].get("authors")
                or not papers_by_id[aid].get("year")
            )
        ]

        # For the demo UI, wait briefly for missing metadata before exposing the card.
        # This preserves the eager raw-query pipeline while avoiding long-lived
        # "Fetching metadata..." placeholders in normal cases.
        if needs_fetch and orch._metadata_fetcher is not None:
            try:
                meta = await asyncio.wait_for(orch._metadata_fetcher(needs_fetch), timeout=2.0)
                for aid, m in meta.items():
                    payload = _paper_payload(aid, m)
                    if payload is None:
                        continue
                    papers_by_id[aid] = payload
                    if m.title and m.authors and m.year:
                        fetched_metadata_ids.add(aid)
            except Exception:
                pass

        papers = [papers_by_id[aid] for aid in ids if aid in papers_by_id]
        await push({"type": "discovery", "query": query, "arxiv_ids": ids, "papers": papers})
        if papers:
            await push({"type": "metadata_update", "query": query, "papers": papers})

        # Fall back to background metadata fetch for anything still missing.
        if needs_fetch:
            t = asyncio.create_task(_fetch_and_push_metadata(needs_fetch, query=query))
            _metadata_tasks.append(t)

    async def on_worker_start(*, state, **_):
        await push({"type": "worker_start", "arxiv_id": state.arxiv_id})

    async def on_worker_done(*, state, result, **_):
        m = result.match
        if m is not None and m.score >= strictness:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": True, "score": m.score,
                        "snippet": m.snippet, "header": m.header_line,
                        "label": m.label})
        else:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": False, "score": m.score if m else 0.0,
                        "snippet": None, "header": None, "label": None})

    async def on_search_done(*, results, matched, latency_s, **_):
        if _metadata_tasks:
            await asyncio.gather(*_metadata_tasks, return_exceptions=True)
        await push({"type": "search_done", "matched": matched,
                    "total": len(results), "latency_s": latency_s})
        await queue.put(None)

    orch.on("search_start",   on_search_start)
    orch.on("discovery_done", on_discovery_done)
    orch.on("worker_start",   on_worker_start)
    orch.on("worker_done",    on_worker_done)
    orch.on("search_done",    on_search_done)

    # ── forager hooks ────────────────────────────────────────────────────────

    async def on_plan_complete(*, plan, reason, **_):
        if plan is None:
            return
        await push({"type": "plan_complete", "arxiv_id": plan.arxiv_id,
                    "header_count": len(plan.headers), "reason": reason})

    forager.on("plan_complete", on_plan_complete)

    # ── run search in background ─────────────────────────────────────────────

    async def _run():
        try:
            await orch.search(query, max_results=max_results, strictness=strictness)
        except Exception as exc:
            await push({"type": "error", "message": str(exc)})
            await queue.put(None)
        finally:
            if orch.tools is not None:
                orch.close()

    task = asyncio.create_task(_run())

    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
    except asyncio.CancelledError:
        task.cancel()
    finally:
        if not task.done():
            task.cancel()
