"""Core discovery protocol and provider access error type."""

from __future__ import annotations

from typing import Protocol


class DiscoveryAccessError(RuntimeError):
    """Raised when a discovery provider cannot be used or is denied."""


class PaperDiscoveryClient(Protocol):
    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]: ...
