"""Librarian orchestration: query planning, discovery, delegation, and result aggregation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic_ai.usage import UsageLimits

from ..agents import ForagerAgent
from ..discovery import PaperMetadataClient, PaperDiscoveryClient
from ..discovery.parsing.arxiv_ids import normalize_arxiv_id
from ..models import SearchResponse, SearchResultEntry
from ..observability import get_logger, trace_span
from ..tools import DiscoveryTools, ExtractionTools
from .discovery_execution import DiscoveryExecutionService
from .query_planner import QueryPlannerService
from .result_policy import IndexedResult, ResultPolicy

log = get_logger("librarian")


@dataclass
class _RuntimeSettings:
    candidate_multiplier: int = 2
    candidate_cap: int = 30
    delegate_concurrency: int = 4
    early_stop_on_matches: bool = True
    agentic_query_loop: bool = True
    max_query_attempts: int = 2
    max_replan_rounds: int = 2


class LibrarianOrchestrator:
    def __init__(
        self,
        *,
        discovery_client: PaperDiscoveryClient,
        forager: ForagerAgent,
        tools: ExtractionTools | None = None,
        discovery_tools: DiscoveryTools | None = None,
        metadata_client: PaperMetadataClient | None = None,
        model_name: str = "test",
        candidate_multiplier: int = 2,
        candidate_cap: int = 30,
        delegate_concurrency: int = 4,
        early_stop_on_matches: bool = True,
        agentic_query_loop: bool = True,
        agentic_discovery: bool = True,
        max_query_attempts: int = 2,
        max_replan_rounds: int = 2,
        query_planner_timeout_seconds: float = 4.0,
        query_planner_model_name: str | None = None,
        planner_request_limit: int = 4,
        planner_total_tokens_limit: int | None = None,
        discovery_request_limit: int = 4,
        discovery_total_tokens_limit: int | None = None,
    ) -> None:
        self.discovery_client = discovery_client
        self.forager = forager
        self.tools = tools
        self._discovery_tools = discovery_tools or DiscoveryTools(chain=discovery_client)
        self._metadata_client = metadata_client

        self._settings = _RuntimeSettings(
            candidate_multiplier=max(1, candidate_multiplier),
            candidate_cap=max(1, candidate_cap),
            delegate_concurrency=max(1, delegate_concurrency),
            early_stop_on_matches=early_stop_on_matches,
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

        self._planner_usage_limits = UsageLimits(
            request_limit=max(1, planner_request_limit),
            total_tokens_limit=planner_total_tokens_limit,
        )
        self._discovery_usage_limits = UsageLimits(
            request_limit=max(1, discovery_request_limit),
            total_tokens_limit=discovery_total_tokens_limit,
        )

        self._query_planner = QueryPlannerService(
            model_name=self._query_planner_model_name,
            enabled=self._agentic_query_loop,
            max_query_attempts=self._settings.max_query_attempts,
            timeout_seconds=query_planner_timeout_seconds,
            usage_limits=self._planner_usage_limits,
        )
        self.query_planner_agent = self._query_planner.agent

        self._discovery_execution = DiscoveryExecutionService(
            model_name=self._query_planner_model_name,
            enabled=self._agentic_discovery,
            discovery_client=self.discovery_client,
            discovery_tools=self._discovery_tools,
            usage_limits=self._discovery_usage_limits,
        )
        self.discovery_agent = self._discovery_execution.agent

        if self.tools is not None and hasattr(self.forager, "set_tools"):
            self.forager.set_tools(self.tools)

    async def search(self, query: str, max_results: int, strictness: float) -> SearchResponse:
        with trace_span("librarian.search", query=query, max_results=max_results, strictness=strictness):
            log.info("search.start query={} max_results={} strictness={}", query, max_results, strictness)
            candidate_budget = min(max_results * self._settings.candidate_multiplier, self._settings.candidate_cap)
            candidate_budget = max(max_results, candidate_budget)

            aggregate_results: dict[str, IndexedResult] = {}
            next_index = 0
            seen_query_keys: set[str] = set()
            seen_queries: list[str] = []
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
                    log.info(
                        "search.ids round={} attempt={} query={} count={} candidate_budget={} ids={}",
                        round_index,
                        executed_attempts,
                        attempt_query,
                        len(arxiv_ids),
                        candidate_budget,
                        arxiv_ids,
                    )

                    indexed_results = await self._run_foragers(
                        arxiv_ids=arxiv_ids,
                        query=attempt_query,
                        strictness=strictness,
                        max_results=max_results,
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
                    if self._settings.early_stop_on_matches and matched >= max_results:
                        log.info(
                            "search.early_stop matched_so_far={} processed={} attempt={} round={}",
                            matched,
                            len(aggregate_results),
                            executed_attempts,
                            round_index,
                        )
                        break

                selected_results = ResultPolicy.rank_and_trim_results(
                    indexed_results=list(aggregate_results.values()),
                    max_results=max_results,
                )
                matched = sum(1 for item in selected_results if item.match is not None)
                if self._settings.early_stop_on_matches and matched >= max_results:
                    break

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
                "search.done discovered={} returned={} matched={} concurrency={} early_stop={} attempts={} agentic_query_loop={}",
                len(aggregate_results),
                len(selected_results),
                matched,
                self._settings.delegate_concurrency,
                self._settings.early_stop_on_matches,
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
        return await self._query_planner.query_attempts(query)

    @staticmethod
    def _query_key(query: str) -> str:
        return QueryPlannerService.query_key(query)

    def _sanitize_attempt_queries(self, base_query: str, planned_queries: list[str]) -> list[str]:
        return self._query_planner.sanitize_attempt_queries(base_query, planned_queries)

    async def _next_replan_seed(self, *, original_query: str, seen_queries: list[str]) -> str | None:
        return await self._query_planner.next_replan_seed(original_query=original_query, seen_queries=seen_queries)

    async def _discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        return await self._discovery_execution.discover_arxiv_ids(query, max_results)

    async def _run_foragers(
        self,
        *,
        arxiv_ids: list[str],
        query: str,
        strictness: float,
        max_results: int,
    ) -> list[IndexedResult]:
        indexed_results: list[IndexedResult] = []
        concurrency = self._settings.delegate_concurrency
        for batch_start in range(0, len(arxiv_ids), concurrency):
            batch = arxiv_ids[batch_start : batch_start + concurrency]
            batch_results = await asyncio.gather(
                *(self._run_one(batch_start + i, arxiv_id, query, strictness) for i, arxiv_id in enumerate(batch))
            )
            indexed_results.extend(batch_results)
            if self._settings.early_stop_on_matches:
                matched_so_far = sum(1 for _, item in indexed_results if item.match is not None)
                if matched_so_far >= max_results:
                    log.info("search.early_stop matched_so_far={} processed={}", matched_so_far, len(indexed_results))
                    break

        indexed_results.sort(key=lambda item: item[0])
        return indexed_results

    async def _attach_metadata(self, results: list[SearchResultEntry]) -> list[SearchResultEntry]:
        if self._metadata_client is None or not results:
            return results

        metadata_by_id = await self._metadata_client.fetch_metadata([item.arxiv_id for item in results])
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
