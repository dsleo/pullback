"""Query planning and replanning service for the librarian orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ..observability import get_agent_instrumentation, get_logger

log = get_logger("librarian.query_planner")


class QueryPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(
        default_factory=list,
        description="Ordered list of concise search queries.",
    )


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
                "You are a query strategist for mathematical paper search with broad knowledge of the literature. "
                "A mathematician provides a statement or result and wants the most similar results in the literature, "
                "possibly phrased differently. Your task is to propose search queries that surface those papers. "
                "Output format: a JSON object with exactly one key 'queries' whose value is a list of strings. "
                "No prose and no extra keys. "
                "Constraints: keep each query short (<= 12 words), preserve the core math topic, "
                "avoid duplicates or trivial rewordings, and do not invent arXiv IDs. "
                "If returning multiple queries, make them meaningfully diverse by varying terminology, "
                "synonyms, or equivalent formulations of the same statement."
            ),
        )

    @staticmethod
    def query_key(query: str) -> str:
        return " ".join(query.lower().split())

    def _simple_attempts(self, base_query: str) -> list[str]:
        if self._max_query_attempts <= 1:
            return [base_query]
        normalized = " ".join(base_query.split())
        hyphen_flat = normalized.replace("-", " ")
        return self.sanitize_attempt_queries(base_query, [hyphen_flat])

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
            return self._simple_attempts(base)

        log.info(
            "query_plan.start model={} timeout_s={:.2f} max_query_attempts={}",
            self._model_name,
            self._timeout_seconds,
            self._max_query_attempts,
        )
        prompt = (
            f"Original query: {base}\n"
            f"Need up to {self._max_query_attempts} search queries in priority order.\n"
            "Goal: maximize recall of papers stating the same or closely related result, "
            "including paraphrased or alternative formulations.\n"
            "Make queries diverse (different phrasing, equivalent terms), not just minor rewrites.\n"
            "Respond only with the JSON object containing 'queries'."
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
            return attempts or [base]
        except Exception as exc:
            log.warning(
                "query_plan.failed model={} timeout_s={:.2f} error_type={} error_repr={} fallback=base_query_only",
                self._model_name,
                self._timeout_seconds,
                type(exc).__name__,
                repr(exc),
            )
            return self._simple_attempts(base)

    async def next_replan_seed(self, *, original_query: str, seen_queries: list[str]) -> str | None:
        seen_keys = {self.query_key(item) for item in seen_queries}
        seen_block = "\n".join(f"- {item}" for item in seen_queries[-8:])
        prompt = (
            "A mathematician is searching for a known statement/result in the literature, "
            "possibly phrased differently.\n"
            f"The orriginal query was: {original_query}\n"
            "You are an expert in mathematical literature search. "
            "You previously proposed the queries below, but they did not return satisfactory matches.\n"
            "Already tried queries:\n"
            f"{seen_block or '- (none)'}\n"
            "Return one new query that targets the same statement/result using different terminology.\n"
            "It must be meaningfully different from the tried queries (not a light rephrase).\n"
            "You may broaden or narrow focus, but keep the same mathematical intent.\n"
            "Respond only with the JSON object containing 'queries'."
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
