"""Librarian orchestration: query planning, discovery, delegation, and result aggregation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic_ai.usage import UsageLimits

from ..agents import ForagerAgent
from ..discovery import PaperMetadataFetcher, PaperDiscoveryClient
from ..discovery.arxiv.ids import normalize_arxiv_id
from ..models import SearchResponse, SearchResultEntry
from ..observability import get_logger, trace_span
from ..tools import DiscoveryTools, ExtractionTools
from .discovery_execution import DiscoveryExecutionService
from .query_planner import QueryPlannerService
from .result_policy import IndexedResult, ResultPolicy

log = get_logger("librarian")


@dataclass
class _RuntimeSettings:
    """Internal knobs stored after validation in __init__."""

    delegate_concurrency: int = 4  # parallel forager calls per batch
    top_k_headers: int = 10  # headers to rescore per paper
    agentic_query_loop: bool = True  # allow the query-planning agent to run
    max_query_attempts: int = 2  # total query attempts including the original
    max_replan_rounds: int = 2  # follow-up replans after empty matches


class LibrarianOrchestrator:
    def __init__(
        self,
        *,
        discovery_client: PaperDiscoveryClient,
        forager: ForagerAgent,
        tools: ExtractionTools | None = None,
        discovery_tools: DiscoveryTools | None = None,
        metadata_fetcher: PaperMetadataFetcher | None = None,
        model_name: str = "test",
        delegate_concurrency: int = 4,
        top_k_headers: int = 10,
        agentic_query_loop: bool = True,
        agentic_discovery: bool = True,
        max_query_attempts: int = 2,
        max_replan_rounds: int = 2,
        timeout_seconds: float = 4.0,
        query_planner_model_name: str | None = None,
    ) -> None:
        self.discovery_client = discovery_client
        self.forager = forager
        self.tools = tools
        self._discovery_tools = discovery_tools or DiscoveryTools(chain=discovery_client)
        self._metadata_fetcher = self._resolve_metadata_fetcher(metadata_fetcher)

        self._settings = _RuntimeSettings(
            delegate_concurrency=max(1, delegate_concurrency),
            top_k_headers=max(1, top_k_headers),
            agentic_query_loop=agentic_query_loop,
            max_query_attempts=max(1, max_query_attempts),
            max_replan_rounds=max(1, max_replan_rounds),
        )

        self._query_planner_model_name = query_planner_model_name or model_name
        self._agentic_query_loop = (
            self._settings.agentic_query_loop
            and self._settings.max_query_attempts > 1
            and self._query_planner_model_name != "test"
        )
        self._agentic_discovery = agentic_discovery and self._query_planner_model_name != "test"

        usage_limits = UsageLimits(request_limit=4)
        self._query_planner = QueryPlannerService(
            model_name=self._query_planner_model_name,
            enabled=self._agentic_query_loop,
            max_query_attempts=self._settings.max_query_attempts,
            timeout_seconds=timeout_seconds,
            usage_limits=usage_limits,
        )
        self.query_planner_agent = self._query_planner.agent

        self._discovery_execution = DiscoveryExecutionService(
            model_name=self._query_planner_model_name,
            enabled=self._agentic_discovery,
            discovery_client=self.discovery_client,
            discovery_tools=self._discovery_tools,
            timeout_seconds=timeout_seconds,
        )
        self.discovery_agent = self._discovery_execution.agent

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

    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        with trace_span("librarian.search", query=query, max_results=max_results, strictness=strictness):
            log.info("search.start query={} max_results={} strictness={}", query, max_results, strictness)
            candidate_budget = max_results

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

                for attempt_query in round_attempts:
                    executed_attempts += 1
                    arxiv_ids = await self._discover_arxiv_ids(attempt_query, candidate_budget)
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
                        query=attempt_query,
                        strictness=strictness,
                    )
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

                if matched > 0:
                    log.info("search.replan_skipped reason=partial_matches matched={} round={}", matched, round_index)
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
                "search.done discovered={} returned={} matched={} concurrency={} attempts={} agentic_query_loop={}",
                len(aggregate_results),
                len(selected_results),
                matched,
                self._settings.delegate_concurrency,
                executed_attempts,
                self._agentic_query_loop,
            )
            return SearchResponse(
                query=query,
                max_results=max_results,
                strictness=strictness,
                results=selected_results,
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

    async def _discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        return await self._discovery_execution.run_discovery(query, max_results)

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
        with trace_span("librarian.delegate", arxiv_id=arxiv_id):
            try:
                match = await self.forager.forage(query=query, arxiv_id=arxiv_id, strictness=strictness)
                return index, SearchResultEntry(arxiv_id=arxiv_id, match=match)
            except Exception as exc:
                log.error(
                    "delegate.failed arxiv_id={} error_type={} error_repr={}",
                    arxiv_id,
                    type(exc).__name__,
                    repr(exc),
                )
                return index, SearchResultEntry(arxiv_id=arxiv_id, match=None)

    def close(self) -> None:
        if self.tools is not None:
            self.tools.close()
