"""Query planning and replanning service for the librarian orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ..observability import get_agent_instrumentation, get_logger

log = get_logger("librarian.query_planner")


class QueryPlanOutput(BaseModel):
    queries: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class QueryPlannerDeps:
    original_query: str
    max_query_attempts: int


class QueryPlannerService:
    def __init__(
        self,
        *,
        model_name: str,
        enabled: bool,
        max_query_attempts: int,
        timeout_seconds: float,
        usage_limits: UsageLimits,
    ) -> None:
        self._enabled = enabled
        self._model_name = model_name
        self._max_query_attempts = max(1, max_query_attempts)
        self._timeout_seconds = timeout_seconds
        self._usage_limits = usage_limits

        self.agent = Agent(
            model_name,
            deps_type=QueryPlannerDeps,
            output_type=QueryPlanOutput,
            instrument=get_agent_instrumentation(),
            instructions=(
                "You are a query strategist for mathematical paper search. "
                "Return concise search queries aimed at discovering arXiv papers likely containing "
                "theorem/lemma/proposition statements for the user's goal. "
                "Produce high-recall rewrites, avoid full sentences, and do not add explanations."
            ),
        )

    @staticmethod
    def query_key(query: str) -> str:
        return " ".join(query.lower().split())

    def sanitize_attempt_queries(self, base_query: str, planned_queries: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for q in [base_query] + planned_queries:
            key = self.query_key(q)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(q.strip())
            if len(ordered) >= self._max_query_attempts:
                break
        return ordered or [base_query]

    async def _run_plan(self, prompt: str, *, original_query: str, max_query_attempts: int) -> QueryPlanOutput:
        if self._timeout_seconds > 0:
            result = await asyncio.wait_for(
                self.agent.run(
                    prompt,
                    deps=QueryPlannerDeps(
                        original_query=original_query,
                        max_query_attempts=max_query_attempts,
                    ),
                    usage_limits=self._usage_limits,
                ),
                timeout=self._timeout_seconds,
            )
        else:
            result = await self.agent.run(
                prompt,
                deps=QueryPlannerDeps(
                    original_query=original_query,
                    max_query_attempts=max_query_attempts,
                ),
                usage_limits=self._usage_limits,
            )
        return result.output

    async def query_attempts(self, query: str) -> list[str]:
        base = query.strip()
        if not base:
            return []
        if not self._enabled:
            return [base]

        log.info(
            "query_plan.start model={} timeout_s={:.2f} max_query_attempts={}",
            self._model_name,
            self._timeout_seconds,
            self._max_query_attempts,
        )
        prompt = (
            f"Original query: {base}\n"
            f"Return up to {self._max_query_attempts} queries in priority order.\n"
            "Queries must target mathematical fixed-point/banach/non-reflexive terminology "
            "and be suitable for semantic search APIs."
        )
        try:
            output = await self._run_plan(
                prompt,
                original_query=base,
                max_query_attempts=self._max_query_attempts,
            )
            planned = [q.strip() for q in output.queries if q and q.strip()]
            attempts = self.sanitize_attempt_queries(base, planned)
            log.info("query_plan.generated attempts={}", attempts)
            return attempts
        except Exception as exc:
            log.warning(
                "query_plan.failed model={} timeout_s={:.2f} error_type={} error_repr={} fallback=base_query_only",
                self._model_name,
                self._timeout_seconds,
                type(exc).__name__,
                repr(exc),
            )
            return [base]

    async def next_replan_seed(self, *, original_query: str, seen_queries: list[str]) -> str | None:
        seen_keys = {self.query_key(item) for item in seen_queries}
        seen_block = "\n".join(f"- {item}" for item in seen_queries[-8:])
        prompt = (
            f"Original query: {original_query}\n"
            "No satisfactory matches were found yet.\n"
            "Already tried queries:\n"
            f"{seen_block or '- (none)'}\n"
            "Return one improved semantic-search query that is materially different from already tried queries.\n"
            "The query should target theorem/lemma discovery in arXiv math papers."
        )
        try:
            output = await self._run_plan(
                prompt,
                original_query=original_query,
                max_query_attempts=1,
            )
            for candidate in [q.strip() for q in output.queries if q and q.strip()]:
                if self.query_key(candidate) not in seen_keys:
                    return candidate
        except Exception as exc:
            log.warning(
                "query_replan.failed model={} timeout_s={:.2f} error_type={} error_repr={}",
                self._model_name,
                self._timeout_seconds,
                type(exc).__name__,
                repr(exc),
            )
        return None
