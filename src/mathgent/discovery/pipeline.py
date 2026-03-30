"""Provider-chain discovery pipeline with timeout handling and dedupe."""

from __future__ import annotations

import asyncio
from typing import Sequence

from ..observability import get_logger
from .base import DiscoveryAccessError, PaperDiscoveryClient
from .arxiv.ids import normalize_arxiv_id

log = get_logger("discovery.pipeline")


class ChainedDiscoveryClient(PaperDiscoveryClient):
    """Simple provider chain: try in order, dedupe, stop at max_results."""

    def __init__(
        self,
        *,
        providers: Sequence[tuple[str, PaperDiscoveryClient]],
        provider_timeout_seconds: float = 8.0,
    ) -> None:
        self._providers = list(providers)
        self._provider_timeout_seconds = max(0.0, provider_timeout_seconds)

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        if max_results <= 0:
            return []

        seen: set[str] = set()
        merged: list[str] = []
        errors: list[str] = []

        for name, provider in self._providers:
            try:
                if self._provider_timeout_seconds > 0:
                    ids = await asyncio.wait_for(
                        provider.discover_arxiv_ids(query, max_results),
                        timeout=self._provider_timeout_seconds,
                    )
                else:
                    ids = await provider.discover_arxiv_ids(query, max_results)
            except TimeoutError:
                error = f"timed out after {self._provider_timeout_seconds:.1f}s"
                errors.append(f"{name}: {error}")
                log.warning("provider.timeout provider={} timeout_s={:.1f}", name, self._provider_timeout_seconds)
                continue
            except DiscoveryAccessError as exc:
                error = str(exc)
                errors.append(f"{name}: {error}")
                log.warning("provider.failed provider={} error_type={} error_repr={}", name, type(exc).__name__, repr(exc))
                continue

            if not ids:
                log.info("provider.empty provider={} query={}", name, query)
                continue

            accepted = 0
            for arxiv_id in ids:
                normalized = normalize_arxiv_id(arxiv_id)
                if not normalized:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                merged.append(normalized)
                accepted += 1
                if len(merged) >= max_results:
                    log.info("provider.success provider={} accepted={} merged={}", name, accepted, merged)
                    return merged

            log.info("provider.success provider={} accepted={} merged={}", name, accepted, merged)

        if merged:
            return merged

        details = " | ".join(errors) if errors else ""
        raise DiscoveryAccessError("No discovery provider returned arXiv IDs." + (f" {details}" if details else ""))
