"""Core discovery protocols and provider access error types."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .arxiv.paper_metadata import PaperMetadata


class DiscoveryAccessError(RuntimeError):
    """Raised when a discovery provider cannot be used or is denied."""


class PaperDiscoveryClient(Protocol):
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]: ...


@runtime_checkable
class SupportsDiscoveryFallback(Protocol):
    async def discover_arxiv_ids_fallback(self, query: str, max_results: int, *, reason: str) -> list[str]: ...


@runtime_checkable
class SupportsDiscoveryMetadata(Protocol):
    def discovery_metadata(self) -> dict[str, PaperMetadata]: ...


@runtime_checkable
class SupportsTitleCandidates(Protocol):
    def title_candidates(self) -> list[str]: ...
