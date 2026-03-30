"""Agent-facing discovery tool facade over the configured provider chain."""

from __future__ import annotations

from dataclasses import dataclass

from ..discovery import PaperDiscoveryClient


@dataclass(frozen=True)
class DiscoveryTools:
    """Minimal discovery facade exposed to orchestration/agents."""

    chain: PaperDiscoveryClient

    async def discover(self, query: str, max_results: int) -> list[str]:
        return await self.chain.discover_arxiv_ids(query, max_results)
