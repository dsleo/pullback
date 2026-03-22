"""Discovery execution service that optionally lets an agent choose discovery tools."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from ..discovery import PaperDiscoveryClient
from ..observability import get_agent_instrumentation, get_logger
from ..tools import DiscoveryTools

log = get_logger("librarian.discovery")


@dataclass(frozen=True)
class DiscoveryDeps:
    discovery_tools: DiscoveryTools


class DiscoveryExecutionService:
    def __init__(
        self,
        *,
        model_name: str,
        enabled: bool,
        discovery_client: PaperDiscoveryClient,
        discovery_tools: DiscoveryTools,
        usage_limits: UsageLimits,
    ) -> None:
        self._enabled = enabled
        self._model_name = model_name
        self._discovery_client = discovery_client
        self._discovery_tools = discovery_tools
        self._usage_limits = usage_limits

        self.agent = Agent(
            model_name,
            deps_type=DiscoveryDeps,
            output_type=list[str],
            instrument=get_agent_instrumentation(),
            instructions=(
                "You find candidate arXiv IDs by calling tools. "
                "Use `discover_papers` to retrieve candidate IDs. "
                "Return only a JSON list of arXiv IDs."
            ),
        )

        @self.agent.tool
        async def discover_papers(
            ctx: RunContext[DiscoveryDeps],
            query: str,
            max_results: int,
        ) -> list[str]:
            """Discover arXiv IDs using the configured provider chain."""
            return await ctx.deps.discovery_tools.discover(query, max_results)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        if not self._enabled:
            log.info("agentic_discovery.disabled fallback=provider_chain")
            return await self._discovery_client.discover_arxiv_ids(query, max_results)

        log.info("agentic_discovery.start model={} max_results={}", self._model_name, max_results)
        prompt = (
            "Find candidate arXiv IDs for the query.\n"
            f"query={query}\n"
            f"max_results={max_results}\n"
            "Call one discovery tool and return only the ID list."
        )
        try:
            result = await self.agent.run(
                prompt,
                deps=DiscoveryDeps(discovery_tools=self._discovery_tools),
                usage_limits=self._usage_limits,
            )
            ids = [item.strip() for item in result.output if isinstance(item, str) and item.strip()]
            if ids:
                log.info("agentic_discovery.success ids={}", ids[:max_results])
                return ids[:max_results]
            log.warning("agentic_discovery.empty fallback=provider_chain")
        except Exception as exc:
            log.warning(
                "agentic_discovery.failed model={} query={} error_type={} error_repr={} fallback=provider_chain",
                self._model_name,
                query,
                type(exc).__name__,
                repr(exc),
            )

        return await self._discovery_client.discover_arxiv_ids(query, max_results)
