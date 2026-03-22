"""Provider-chain discovery pipeline with timeout handling, dedupe, and round-robin merge."""

from __future__ import annotations

import asyncio
from typing import Sequence

from ..observability import get_logger
from .base import DiscoveryAccessError, PaperDiscoveryClient
from .parsing.arxiv_ids import normalize_arxiv_id

log = get_logger("discovery.pipeline")


class ChainedDiscoveryClient(PaperDiscoveryClient):
    def __init__(
        self,
        *,
        providers: Sequence[tuple[str, PaperDiscoveryClient]],
        provider_timeout_seconds: float = 8.0,
    ) -> None:
        self._providers = list(providers)
        self._provider_timeout_seconds = provider_timeout_seconds

    @staticmethod
    def _merge_round_robin(groups: list[list[str]], limit: int) -> list[str]:
        merged: list[str] = []
        if not groups or limit <= 0:
            return merged
        max_len = max((len(group) for group in groups), default=0)
        for idx in range(max_len):
            for group in groups:
                if idx < len(group):
                    merged.append(group[idx])
                    if len(merged) >= limit:
                        return merged
        return merged

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        provider_groups: list[list[str]] = []

        for name, provider in self._providers:
            try:
                merged_so_far = self._merge_round_robin(provider_groups, max_results)
                remaining = max_results - len(merged_so_far)
                if remaining <= 0:
                    break

                request_size = min(max(remaining * 3, remaining), 50)
                if self._provider_timeout_seconds > 0:
                    ids = await asyncio.wait_for(
                        provider.discover_arxiv_ids(query, request_size),
                        timeout=self._provider_timeout_seconds,
                    )
                else:
                    ids = await provider.discover_arxiv_ids(query, request_size)
            except TimeoutError:
                errors.append(f"{name}: timed out after {self._provider_timeout_seconds:.1f}s")
                log.warning(
                    "provider.timeout provider={} timeout_s={:.1f} query={} request_size={}",
                    name,
                    self._provider_timeout_seconds,
                    query,
                    request_size,
                )
                continue
            except DiscoveryAccessError as exc:
                errors.append(f"{name}: {exc}")
                log.warning(
                    "provider.failed provider={} query={} error_type={} error_repr={}",
                    name,
                    query,
                    type(exc).__name__,
                    repr(exc),
                )
                continue

            if not ids:
                log.info("provider.empty provider={} query={}", name, query)
                continue

            accepted: list[str] = []
            for arxiv_id in ids:
                normalized = normalize_arxiv_id(arxiv_id)
                if normalized in seen:
                    continue
                seen.add(normalized)
                accepted.append(normalized)

            if accepted:
                provider_groups.append(accepted)

            merged = self._merge_round_robin(provider_groups, max_results)
            log.info(
                "provider.success provider={} ids={} accepted={} merged={}",
                name,
                ids,
                len(accepted),
                merged,
            )
            if len(merged) >= max_results:
                return merged

        merged_final = self._merge_round_robin(provider_groups, max_results)
        if merged_final:
            return merged_final

        details = " | ".join(errors) if errors else ""
        raise DiscoveryAccessError("No discovery provider returned arXiv IDs." + (f" {details}" if details else ""))
