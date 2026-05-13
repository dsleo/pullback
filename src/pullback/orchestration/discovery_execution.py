"""Discovery execution service that optionally lets an agent choose discovery tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from ..discovery import DiscoveryAccessError, PaperDiscoveryClient
from ..observability import get_agent_instrumentation, get_logger
from ..tools import DiscoveryTools

log = get_logger("librarian.discovery")


@dataclass(frozen=True)
class DiscoveryDeps:
    discovery_tools: DiscoveryTools


class DiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arxiv_ids: list[str] = Field(
        default_factory=list,
        description="List of arXiv IDs (e.g., '2401.01234', 'math/0309136').",
    )


class DiscoveryExecutionService:
    """Run discovery deterministically or through a minimal tool-using agent wrapper."""

    def __init__(
        self,
        *,
        model_name: str,
        enabled: bool,
        discovery_client: PaperDiscoveryClient,
        discovery_tools: DiscoveryTools,
        timeout_seconds: float = 4.0,
    ) -> None:
        self._enabled = enabled
        self._model_name = model_name
        self._discovery_client = discovery_client
        self._discovery_tools = discovery_tools
        self._timeout_seconds = max(0.0, timeout_seconds)
        self._usage_limits = UsageLimits(request_limit=4)

        self.agent = Agent(
            model_name,
            deps_type=DiscoveryDeps,
            output_type=DiscoveryOutput,
            instrument=get_agent_instrumentation(),
            instructions=(
                "You are a discovery assistant for mathematical papers. "
                "Your task is to return relevant arXiv IDs for a query. "
                "Call the discovery tool exactly once, then return a JSON object "
                "with exactly one key: 'arxiv_ids', a list of strings. "
                "Return no prose and no extra keys."
            ),
        )
        # Tool callable only by the agent during agent.run(...). Not used directly by app code.
        self.agent.tool(self.discover_papers_tool)

    async def discover_papers_tool(
        self,
        ctx: RunContext[DiscoveryDeps],
        query: str,
        max_results: int,
    ) -> list[str]:
        """Agent-only tool: discover arXiv IDs using the configured provider chain."""
        try:
            return await ctx.deps.discovery_tools.discover(query, max_results)
        except DiscoveryAccessError as exc:
            log.warning(
                "agentic_discovery.tool_error query={} max_results={} error={} returning_empty_ids",
                query,
                max_results,
                exc,
            )
            return []

    async def run_discovery(self, query: str, max_results: int) -> list[str]:
        """Public entry point used by the orchestrator."""
        if not self._enabled:
            return await self._discover_via_chain(query, max_results)

        log.info("agentic_discovery.start model={} max_results={}", self._model_name, max_results)
        prompt = (
            "Your task is to find candidate arXiv IDs for the query below.\n"
            f"query={query}\n"
            f"max_results={max_results}\n"
            "Use the discovery tool once and return only the JSON object with key 'arxiv_ids'. "
            "No extra keys or text."
        )
        try:
            if self._timeout_seconds > 0:
                result = await asyncio.wait_for(
                    self.agent.run(
                        prompt,
                        deps=DiscoveryDeps(discovery_tools=self._discovery_tools),
                        usage_limits=self._usage_limits,
                    ),
                    timeout=self._timeout_seconds,
                )
            else:
                result = await self.agent.run(
                    prompt,
                    deps=DiscoveryDeps(discovery_tools=self._discovery_tools),
                    usage_limits=self._usage_limits,
                )
            ids = [item.strip() for item in result.output.arxiv_ids if isinstance(item, str) and item.strip()]
            if ids:
                log.info("agentic_discovery.success ids={}", ids[:max_results])
                return ids[:max_results]
            log.warning("agentic_discovery.empty fallback=provider_chain")
        except Exception as exc:
            log.warning(
                "agentic_discovery.failed model={} timeout_s={:.2f} query={} error_type={} error_repr={} fallback=provider_chain",
                self._model_name,
                self._timeout_seconds,
                query,
                type(exc).__name__,
                repr(exc),
            )

        return await self._discover_via_chain(query, max_results)

    async def _discover_via_chain(self, query: str, max_results: int) -> list[str]:
        """Deterministic fallback: call the provider chain directly."""
        try:
            return await self._discovery_client.discover_arxiv_ids(query, max_results)
        except DiscoveryAccessError as exc:
            log.warning(
                "agentic_discovery.chain_error query={} max_results={} error={} returning_empty_ids",
                query,
                max_results,
                exc,
            )
            return []
