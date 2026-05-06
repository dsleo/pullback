"""Forager execution: extract statement blocks, rerank, and threshold."""

from __future__ import annotations

from dataclasses import dataclass
from ..observability.hooks import HookRegistry
from ..models import LemmaMatch, LemmaHeader
from ..observability import get_logger, logfire_info, trace_span
from ..rerank import Reranker, TokenOverlapReranker
from ..tools import ExtractionTools

log = get_logger("forager")

FORAGER_HOOK_EVENTS = (
    "plan_start",
    "plan_complete",
    "execute_start",
    "snippet_scored",
    "execute_complete",
)


@dataclass(frozen=True)
class ForagePlan:
    query: str
    arxiv_id: str
    strictness: float
    headers: list[LemmaHeader]


class ForagerAgent:
    def __init__(
        self,
        *,
        reranker: Reranker | None = None,
        tools: ExtractionTools | None = None,
        top_k_headers: int = 10,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._reranker = reranker or TokenOverlapReranker()
        self._tools = tools
        self._top_k_headers = max(1, top_k_headers)
        self._hooks = hooks or HookRegistry(allowed_events=FORAGER_HOOK_EVENTS, name="forager.hooks")

    def set_tools(self, tools: ExtractionTools) -> None:
        self._tools = tools

    def on(self, event: str, handler) -> None:
        self._hooks.on(event, handler)

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> list[LemmaMatch]:
        if self._tools is None:
            raise RuntimeError("Forager tools are not configured.")

        with trace_span("forager.forage", query=query, arxiv_id=arxiv_id, strictness=strictness):
            plan = await self.plan(query=query, arxiv_id=arxiv_id, strictness=strictness)
            if plan is None:
                return []
            return await self.execute(plan)

    async def plan(self, *, query: str, arxiv_id: str, strictness: float) -> ForagePlan | None:
        if self._tools is None:
            raise RuntimeError("Forager tools are not configured.")

        import time
        plan_start = time.perf_counter()
        await self._hooks.emit("plan_start", query=query, arxiv_id=arxiv_id, strictness=strictness)
        with trace_span("forager.plan", query=query, arxiv_id=arxiv_id):
            tools = self._tools
            log.info("forage.plan_start arxiv_id={} strictness={}", arxiv_id, strictness)
            headers = await tools.get_paper_headers(arxiv_id)
            if not headers:
                log.info("forage.no_headers arxiv_id={}", arxiv_id)
                plan_time = time.perf_counter() - plan_start
                await self._hooks.emit("plan_complete", plan=None, reason="no_headers", plan_time_s=round(plan_time, 4))
                return None
            plan = ForagePlan(
                query=query,
                arxiv_id=arxiv_id,
                strictness=strictness,
                headers=headers,
            )
            plan_time = time.perf_counter() - plan_start
            await self._hooks.emit("plan_complete", plan=plan, reason=None, plan_time_s=round(plan_time, 4))
            return plan

    async def execute(self, plan: ForagePlan) -> list[LemmaMatch]:
        if self._tools is None:
            raise RuntimeError("Forager tools are not configured.")

        import time
        exec_start = time.perf_counter()
        await self._hooks.emit("execute_start", plan=plan)
        with trace_span("forager.execute", arxiv_id=plan.arxiv_id):
            tools = self._tools
            snippets: dict[int, str] = {}
            fetch_bulk = getattr(tools, "fetch_header_blocks", None)
            fetch_time = 0.0
            if callable(fetch_bulk):
                fetch_start = time.perf_counter()
                snippets = await fetch_bulk(plan.arxiv_id, plan.headers, context_lines=20)
                fetch_time = time.perf_counter() - fetch_start

            # Collect all (header, snippet) pairs before scoring so we can batch.
            header_snippets: list[tuple[LemmaHeader, str]] = []
            for header in plan.headers:
                snippet = snippets.get(header.line_number, "")
                if not snippet:
                    snippet = await tools.fetch_header_block(
                        plan.arxiv_id,
                        header.line_number,
                        header.line,
                        context_lines=20,
                    )
                header_snippets.append((header, snippet))

            all_snippets = [s for _, s in header_snippets]
            scores = self._reranker.score_batch(plan.query, all_snippets)

            matches: list[LemmaMatch] = []
            for (header, snippet), snippet_score in zip(header_snippets, scores):
                log.info("forage.header_selected arxiv_id={} selected_line={}", plan.arxiv_id, header.line_number)
                log.info("forage.scored arxiv_id={} score={:.4f}", plan.arxiv_id, snippet_score)
                logfire_info("forager scored snippet", arxiv_id=plan.arxiv_id, score=snippet_score)
                await self._hooks.emit(
                    "snippet_scored",
                    plan=plan,
                    header=header,
                    score=snippet_score,
                    snippet=snippet,
                )
                matches.append(LemmaMatch(
                    arxiv_id=plan.arxiv_id,
                    line_number=header.line_number,
                    header_line=header.line,
                    snippet=snippet,
                    score=snippet_score,
                ))

            # Keep top-k by score
            matches.sort(key=lambda m: m.score, reverse=True)
            matches = matches[: self._top_k_headers]

            exec_time = time.perf_counter() - exec_start
            score_time = exec_time - fetch_time

            best_score = matches[0].score if matches else 0.0
            await self._hooks.emit(
                "execute_complete", plan=plan, result=matches[0] if matches else None, score=best_score,
                execute_time_s=round(exec_time, 4), fetch_time_s=round(fetch_time, 4), score_time_s=round(score_time, 4)
            )
            return matches
