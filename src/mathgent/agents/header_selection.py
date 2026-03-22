"""Header selection strategies for forager extraction (heuristic or LLM-based)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ..models import LemmaHeader
from ..observability import get_agent_instrumentation, get_logger

log = get_logger("forager.selector")


class HeaderSelector(Protocol):
    async def select_line(
        self,
        *,
        query: str,
        arxiv_id: str,
        headers: list[LemmaHeader],
        heuristic_line: int,
    ) -> int | None: ...


class HeuristicHeaderSelector(HeaderSelector):
    async def select_line(
        self,
        *,
        query: str,
        arxiv_id: str,
        headers: list[LemmaHeader],
        heuristic_line: int,
    ) -> int | None:
        _ = query, arxiv_id, headers
        return heuristic_line


class _HeaderSelectionOutput(BaseModel):
    line_number: int


@dataclass(frozen=True)
class _HeaderSelectorDeps:
    query: str
    arxiv_id: str
    heuristic_line: int


class LLMHeaderSelector(HeaderSelector):
    def __init__(
        self,
        model_name: str,
        *,
        request_limit: int = 3,
        total_tokens_limit: int | None = None,
    ) -> None:
        self._usage_limits = UsageLimits(
            request_limit=request_limit,
            total_tokens_limit=total_tokens_limit,
        )
        self._agent = Agent(
            model_name,
            deps_type=_HeaderSelectorDeps,
            output_type=_HeaderSelectionOutput,
            instrument=get_agent_instrumentation(),
            instructions=(
                "You select one theorem-like header for a query. "
                "Return exactly one candidate line number from the provided list."
            ),
        )

    async def select_line(
        self,
        *,
        query: str,
        arxiv_id: str,
        headers: list[LemmaHeader],
        heuristic_line: int,
    ) -> int | None:
        headers_text = "\n".join(f"{h.line_number}: {h.line}" for h in headers)
        prompt = (
            "Choose the single best candidate header line for the query.\n"
            f"Query: {query}\n"
            f"arXiv: {arxiv_id}\n"
            f"Fallback line: {heuristic_line}\n"
            "Candidates:\n"
            f"{headers_text}\n"
        )
        try:
            result = await self._agent.run(
                prompt,
                deps=_HeaderSelectorDeps(
                    query=query,
                    arxiv_id=arxiv_id,
                    heuristic_line=heuristic_line,
                ),
                usage_limits=self._usage_limits,
            )
            return int(result.output.line_number)
        except Exception as exc:
            log.warning("selector.llm_failed arxiv_id={} error={}", arxiv_id, exc)
            return None
