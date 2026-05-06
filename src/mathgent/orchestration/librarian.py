"""Librarian orchestration: query planning, discovery, delegation, and result aggregation."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pydantic_ai.usage import UsageLimits

from ..agents import ForagerAgent
from ..discovery import DiscoveryAccessError, PaperMetadataFetcher, PaperDiscoveryClient
from ..discovery.arxiv.ids import normalize_arxiv_id
from ..models import SearchResponse, SearchResultEntry, LemmaMatch
from ..rerank import OpenAIEmbeddingReranker
from ..observability.hooks import HookRegistry
from ..observability import get_logger, trace_span
from ..tools import ExtractionTools
from .query_planner import QueryPlannerService
from .result_policy import IndexedResult, ResultPolicy

log = get_logger("librarian")

LIBRARIAN_HOOK_EVENTS = (
    "search_start",
    "search_done",
    "discovery_start",
    "discovery_done",
    "worker_start",
    "worker_done",
)


@dataclass
class _RuntimeSettings:
    """Internal knobs stored after validation in __init__."""

    delegate_concurrency: int = 4  # parallel forager calls per batch
    top_k_headers: int = 10  # headers to rescore per paper
    agentic: bool = True  # enable agentic query loop + discovery wrapper
    max_query_attempts: int = 2  # total query attempts including the original
    max_replan_rounds: int = 2  # follow-up replans after empty matches


@dataclass
class PaperWorkerState:
    index: int
    arxiv_id: str
    query: str
    strictness: float
    started_at: float | None = None
    finished_at: float | None = None
    error: Exception | None = None


class LibrarianOrchestrator:
    """Orchestrates the full theorem-search pipeline for a single query.

    Pipeline stages:
    1. **Query planning** — LLM expands the user query into 3-4 diverse variants
       (paper-style, statement-style, keyword-style, entity-attribution).
    2. **Discovery** — Each variant is sent to the active provider chain
       (OpenAlex, zbMATH, arXiv API, Semantic Scholar) in parallel; arXiv IDs
       are collected and deduplicated across providers and attempts.
    3. **Foraging** — For each candidate paper, a ForagerAgent fetches theorem-like
       headers from the LaTeX source, extracts the best-matching block, and scores
       it against the query. Papers scoring below `strictness` are dropped.
    4. **Reranking** — Surviving results are ranked by their match score and the
       top `max_results` are returned, enriched with title/author metadata.

    If no results are found after the first attempt, the orchestrator can replan
    (up to `max_replan_rounds`) by generating new query variants targeting gaps
    identified in previous rounds.

    The "librarian" metaphor: this class knows where to look, which questions to
    ask, and how to evaluate what comes back — it delegates the actual digging to
    the ForagerAgent.
    """

    def __init__(
        self,
        *,
        discovery_client: PaperDiscoveryClient,
        forager: ForagerAgent,
        tools: ExtractionTools | None = None,
        metadata_fetcher: PaperMetadataFetcher | None = None,
        model_name: str = "test",
        delegate_concurrency: int = 4,
        top_k_headers: int = 10,
        agentic: bool = True,
        max_query_attempts: int = 2,
        max_replan_rounds: int = 2,
        timeout_seconds: float = 4.0,
        query_planner_model_name: str | None = None,
        ranking_strategy: str = "token",
    ) -> None:
        self.discovery_client = discovery_client
        self.forager = forager
        self.tools = tools
        self._metadata_fetcher = self._resolve_metadata_fetcher(metadata_fetcher)
        self._ranking_strategy = ranking_strategy

        self._settings = _RuntimeSettings(
            delegate_concurrency=max(1, delegate_concurrency),
            top_k_headers=max(1, top_k_headers),
            agentic=agentic,
            max_query_attempts=max(1, max_query_attempts),
            max_replan_rounds=max(1, max_replan_rounds),
        )

        self._query_planner_model_name = query_planner_model_name or model_name
        self._agentic_query_loop = (
            self._settings.agentic
            and self._settings.max_query_attempts > 1
            and self._query_planner_model_name != "test"
        )

        usage_limits = UsageLimits(request_limit=4)
        self._query_planner = QueryPlannerService(
            model_name=self._query_planner_model_name,
            enabled=self._query_planner_model_name != "test",
            max_query_attempts=self._settings.max_query_attempts,
            timeout_seconds=timeout_seconds,
            usage_limits=usage_limits,
        )
        self.query_planner_agent = self._query_planner.agent
        self._hooks = HookRegistry(allowed_events=LIBRARIAN_HOOK_EVENTS, name="librarian.hooks")

        if self.tools is not None and hasattr(self.forager, "set_tools"):
            self.forager.set_tools(self.tools)

    @staticmethod
    def _resolve_metadata_fetcher(
        metadata_fetcher: PaperMetadataFetcher | object | None,
    ) -> PaperMetadataFetcher | None:
        if metadata_fetcher is None:
            return None
        if callable(metadata_fetcher):
            return metadata_fetcher
        fetch_method = getattr(metadata_fetcher, "fetch_metadata", None)
        if callable(fetch_method):
            return fetch_method
        raise TypeError("metadata_fetcher must be callable or expose fetch_metadata(arxiv_ids).")

    def on(self, event: str, handler) -> None:
        self._hooks.on(event, handler)

    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        with trace_span("librarian.search", query=query, max_results=max_results, strictness=strictness):
            start_time = time.perf_counter()
            await self._hooks.emit(
                "search_start",
                query=query,
                max_results=max_results,
                strictness=strictness,
            )
            log.info("search.start query={} max_results={} strictness={}", query, max_results, strictness)
            candidate_budget = max_results

            paper_query: str | None = None
            statement_query: str | None = None
            forager_query = query

            aggregate_results: dict[str, IndexedResult] = {}
            next_index = 0
            seen_query_keys: set[str] = set()
            seen_queries: list[str] = []
            seen_arxiv_ids: set[str] = set()
            executed_attempts = 0
            max_rounds = self._settings.max_replan_rounds if self._agentic_query_loop else 1
            seed_query = query

            for round_index in range(1, max_rounds + 1):
                planned_attempts = await self._query_attempts(seed_query)
                round_attempts: list[str] = []
                for candidate in planned_attempts:
                    key = self._query_key(candidate)
                    if not key or key in seen_query_keys:
                        continue
                    seen_query_keys.add(key)
                    seen_queries.append(candidate)
                    round_attempts.append(candidate)

                if not round_attempts:
                    log.info("search.no_new_attempts round={} seed_query={}", round_index, seed_query)
                    break

                for attempt_index, attempt_query in enumerate(round_attempts):
                    executed_attempts += 1
                    is_raw_query = (attempt_index == 0 and round_index == 1)
                    arxiv_ids = await self._discover_arxiv_ids(attempt_query, candidate_budget, is_raw_query=is_raw_query)
                    fresh_ids: list[str] = []
                    for arxiv_id in arxiv_ids:
                        if arxiv_id in seen_arxiv_ids:
                            continue
                        seen_arxiv_ids.add(arxiv_id)
                        fresh_ids.append(arxiv_id)
                    log.info(
                        "search.ids round={} attempt={} query={} count={} candidate_budget={} ids={}",
                        round_index,
                        executed_attempts,
                        attempt_query,
                        len(fresh_ids),
                        candidate_budget,
                        fresh_ids,
                    )

                    indexed_results = await self._run_foragers(
                        arxiv_ids=fresh_ids,
                        query=forager_query,
                        strictness=strictness,
                    )
                    
                    # Instead of just merging paper-level results, we aggregate all candidates
                    # and rerank them across all papers using the slow pass (OpenAI embeddings).
                    all_candidates: list[LemmaMatch] = []
                    for _, res in indexed_results:
                        all_candidates.extend(res.candidates)
                    
                    if all_candidates:
                        with trace_span("librarian.global_rerank", count=len(all_candidates)):
                            # Truncate snippets to avoid exceeding OpenAI's 8192 token limit
                            # Rough estimate: ~1 char ≈ 0.25 tokens, so limit to ~8000 chars per snippet to be safe
                            max_snippet_chars = 8000
                            candidate_snippets = [c.snippet[:max_snippet_chars] if c.snippet else "" for c in all_candidates]

                            # Use dedicated OpenAIEmbeddingReranker for global pass if the system is configured for semantic ranking.
                            # We check if we should do this based on the strategy.
                            if self._ranking_strategy == "hybrid_token_openai" and not forager_query.startswith("test:"):
                                reranker = OpenAIEmbeddingReranker()
                                try:
                                    global_scores = reranker.score_batch(forager_query, candidate_snippets)
                                    for candidate, score in zip(all_candidates, global_scores):
                                        candidate.score = score
                                except Exception as e:
                                    log.warning("global_rerank.failed error={}", e)
                                    # Fallback: keep existing scores from forager
                        
                        # Re-organize candidate results back into indexed_results
                        # For each paper, we take the best match after global scoring
                        for _, res in indexed_results:
                            res.candidates.sort(key=lambda m: m.score, reverse=True)
                            res.match = res.candidates[0] if res.candidates else None

                    next_index = ResultPolicy.merge_indexed_results(
                        aggregate_results=aggregate_results,
                        incoming_results=indexed_results,
                        next_index=next_index,
                    )

                    selected_results = ResultPolicy.rank_and_trim_results(
                        indexed_results=list(aggregate_results.values()),
                        max_results=max_results,
                    )
                    matched = sum(1 for item in selected_results if item.match is not None)

                if matched >= max_results:
                    log.info("search.replan_skipped reason=full_results matched={} round={}", matched, round_index)
                    break

                if round_index >= max_rounds:
                    break

                next_seed = await self._next_replan_seed(
                    original_query=query,
                    seen_queries=seen_queries,
                )
                if not next_seed:
                    log.info("search.replan_stop reason=no_next_seed round={}", round_index)
                    break
                seed_query = next_seed

            selected_results = ResultPolicy.rank_and_trim_results(
                indexed_results=list(aggregate_results.values()),
                max_results=max_results,
            )
            selected_results = await self._attach_metadata(selected_results)

            matched = sum(1 for item in selected_results if item.match is not None)
            log.info(
                "search.done discovered={} returned={} matched={} concurrency={} attempts={} agentic={}",
                len(aggregate_results),
                len(selected_results),
                matched,
                self._settings.delegate_concurrency,
                executed_attempts,
                self._settings.agentic,
            )
            await self._hooks.emit(
                "search_done",
                query=query,
                max_results=max_results,
                strictness=strictness,
                results=selected_results,
                matched=matched,
                latency_s=time.perf_counter() - start_time,
            )
            return SearchResponse(
                query=query,
                max_results=max_results,
                strictness=strictness,
                results=selected_results,
                paper_query=paper_query,
                statement_query=statement_query,
                discovery_queries=seen_queries,
                forager_query=forager_query,
            )

    async def _query_attempts(self, query: str) -> list[str]:
        if self._settings.max_query_attempts <= 1:
            base = query.strip()
            return [base] if base else []
        return await self._query_planner.query_attempts(query)

    @staticmethod
    def _query_key(query: str) -> str:
        return QueryPlannerService.query_key(query)

    async def _next_replan_seed(self, *, original_query: str, seen_queries: list[str]) -> str | None:
        return await self._query_planner.next_replan_seed(original_query=original_query, seen_queries=seen_queries)

    async def _discover_arxiv_ids(self, query: str, max_results: int, *, is_raw_query: bool = True) -> list[str]:
        await self._hooks.emit("discovery_start", query=query, max_results=max_results)
        try:
            from ..discovery.pipeline import ChainedDiscoveryClient
            if isinstance(self.discovery_client, ChainedDiscoveryClient):
                ids = await self.discovery_client.discover_arxiv_ids(query, max_results, is_raw_query=is_raw_query)
            else:
                ids = await self.discovery_client.discover_arxiv_ids(query, max_results)
        except DiscoveryAccessError as exc:
            log.warning("discovery.failed query={} error={} returning_empty_ids", query, exc)
            ids = []
        provider_timeouts = getattr(self.discovery_client, "timeout_counts", {})
        await self._hooks.emit(
            "discovery_done",
            query=query,
            max_results=max_results,
            arxiv_ids=ids,
            provider_timeouts=provider_timeouts,
        )
        return ids

    async def _run_foragers(
        self,
        *,
        arxiv_ids: list[str],
        query: str,
        strictness: float,
    ) -> list[IndexedResult]:
        indexed_results: list[IndexedResult] = []
        concurrency = self._settings.delegate_concurrency
        for batch_start in range(0, len(arxiv_ids), concurrency):
            batch = arxiv_ids[batch_start : batch_start + concurrency]
            batch_results = await asyncio.gather(
                *(self._run_one(batch_start + i, arxiv_id, query, strictness) for i, arxiv_id in enumerate(batch))
            )
            indexed_results.extend(batch_results)

        indexed_results.sort(key=lambda item: item[0])
        return indexed_results

    async def _attach_metadata(self, results: list[SearchResultEntry]) -> list[SearchResultEntry]:
        if self._metadata_fetcher is None or not results:
            return results

        metadata_by_id = await self._metadata_fetcher([item.arxiv_id for item in results])
        if not metadata_by_id:
            return results

        enriched: list[SearchResultEntry] = []
        for item in results:
            metadata = metadata_by_id.get(normalize_arxiv_id(item.arxiv_id))
            if metadata is None:
                enriched.append(item)
                continue
            enriched.append(
                item.model_copy(
                    update={
                        "title": metadata.title,
                        "authors": metadata.authors,
                    }
                )
            )
        return enriched

    async def _run_one(
        self,
        index: int,
        arxiv_id: str,
        query: str,
        strictness: float,
    ) -> IndexedResult:
        state = PaperWorkerState(
            index=index,
            arxiv_id=arxiv_id,
            query=query,
            strictness=strictness,
            started_at=time.perf_counter(),
        )
        await self._hooks.emit("worker_start", state=state)
        with trace_span("librarian.worker", arxiv_id=arxiv_id, index=index):
            try:
                matches = await self.forager.forage(query=query, arxiv_id=arxiv_id, strictness=strictness)
                # Store the best match and all candidates
                best_match = matches[0] if matches else None
                result = SearchResultEntry(arxiv_id=arxiv_id, match=best_match, candidates=matches)
                state.finished_at = time.perf_counter()
                await self._hooks.emit("worker_done", state=state, result=result)
                if self.tools is not None:
                    await self.tools.delete_paper(arxiv_id)
                return index, result
            except Exception as exc:
                log.error(
                    "delegate.failed arxiv_id={} error_type={} error_repr={}",
                    arxiv_id,
                    type(exc).__name__,
                    repr(exc),
                )
                state.error = exc
                state.finished_at = time.perf_counter()
                result = SearchResultEntry(arxiv_id=arxiv_id, match=None, candidates=[])
                await self._hooks.emit("worker_done", state=state, result=result)
                return index, result

    def close(self) -> None:
        if self.tools is not None:
            self.tools.close()
