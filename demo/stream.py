"""SSE stream generator for the mathgent demo."""

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
        await push({"type": "queries_planned", "queries": attempts})
        return attempts

    orch._query_attempts = _patched_query_attempts  # type: ignore[method-assign]

    # ── librarian hooks ──────────────────────────────────────────────────────

    async def on_search_start(*, query, max_results, **_):
        await push({"type": "query_start", "query": query,
                    "max_results": max_results, "strictness": strictness})

    # Track metadata fetches — awaited before the stream sentinel
    fetched_metadata_ids: set[str] = set()
    _metadata_tasks: list[asyncio.Task] = []

    async def _fetch_and_push_metadata(ids: list[str]) -> None:
        if orch._metadata_fetcher is None:
            return
        new_ids = [aid for aid in ids if aid not in fetched_metadata_ids]
        if not new_ids:
            return
        fetched_metadata_ids.update(new_ids)
        try:
            meta = await orch._metadata_fetcher(new_ids)
            if meta:
                papers = [
                    {"arxiv_id": aid, "title": m.title, "authors": list(m.authors or [])}
                    for aid, m in meta.items()
                    if m.title
                ]
                if papers:
                    await push({"type": "metadata_update", "papers": papers})
        except Exception:
            pass  # metadata is best-effort

    async def on_discovery_done(*, query, arxiv_ids, metadata=None, **_):
        await push({"type": "discovery", "query": query, "arxiv_ids": list(arxiv_ids)})
        # Use metadata the provider already fetched (e.g. OpenAlex returns titles inline).
        if metadata:
            papers = [
                {"arxiv_id": aid, "title": m.title, "authors": list(m.authors or [])}
                for aid, m in metadata.items()
                if m.title
            ]
            if papers:
                await push({"type": "metadata_update", "papers": papers})
            fetched_metadata_ids.update(metadata.keys())
        # Fall back to arXiv API for any IDs the providers didn't supply metadata for.
        remaining = [aid for aid in arxiv_ids if aid not in fetched_metadata_ids]
        if remaining:
            t = asyncio.create_task(_fetch_and_push_metadata(remaining))
            _metadata_tasks.append(t)

    async def on_worker_start(*, state, **_):
        await push({"type": "worker_start", "arxiv_id": state.arxiv_id})

    async def on_worker_done(*, state, result, **_):
        m = result.match
        if m is not None and m.score >= strictness:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": True, "score": m.score,
                        "snippet": m.snippet, "header": m.header_line})
        else:
            await push({"type": "execute_complete", "arxiv_id": state.arxiv_id,
                        "matched": False, "score": m.score if m else 0.0,
                        "snippet": None, "header": None})

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
