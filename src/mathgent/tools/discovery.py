"""Agent-facing discovery tool facade over chained and provider-specific search clients."""

from __future__ import annotations

from dataclasses import dataclass

from ..discovery import ExaDiscoveryClient, OpenAlexDiscoveryClient, PaperDiscoveryClient


@dataclass(frozen=True)
class DiscoveryTools:
    """Standardized search tools facade for discovery providers.
    """

    chain: PaperDiscoveryClient
    openalex: OpenAlexDiscoveryClient | None = None
    exa: ExaDiscoveryClient | None = None

    async def discover(self, query: str, max_results: int) -> list[str]:
        """Discover arXiv IDs with the configured chained provider strategy."""
        return await self.chain.discover_arxiv_ids(query, max_results)

    async def discover_openalex(self, query: str, max_results: int) -> list[str]:
        """Discover arXiv IDs directly from OpenAlex semantic search."""
        if self.openalex is None:
            return []
        return await self.openalex.discover_arxiv_ids(query, max_results)

    async def discover_exa(self, query: str, max_results: int) -> list[str]:
        """Discover arXiv IDs directly from Exa search."""
        if self.exa is None:
            return []
        return await self.exa.discover_arxiv_ids(query, max_results)
