"""Provider-chain discovery pipeline with parallel execution, timeout handling, and dedupe."""

from __future__ import annotations

import asyncio
from typing import Sequence

from ..observability import get_logger
from .base import DiscoveryAccessError, PaperDiscoveryClient
from .arxiv.ids import normalize_arxiv_id
from .arxiv.metadata import PaperMetadata

log = get_logger("discovery.pipeline")


_DEGRADED_THRESHOLD = 3   # consecutive timeouts before logging degradation warning
_CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive timeouts before skipping provider entirely


class ChainedDiscoveryClient(PaperDiscoveryClient):
    """Parallel provider fan-out: all providers run concurrently, results are merged in order."""

    def __init__(
        self,
        *,
        providers: Sequence[tuple[str, PaperDiscoveryClient]],
        provider_timeout_seconds: float = 8.0,
        raw_only_providers: frozenset[str] = frozenset(),
    ) -> None:
        self._providers = list(providers)
        self._provider_timeout_seconds = max(0.0, provider_timeout_seconds)
        self._raw_only_providers = raw_only_providers
        # timeout tracking: cumulative count and consecutive streak per provider
        self._timeout_counts: dict[str, int] = {name: 0 for name, _ in self._providers}
        self._consecutive_timeouts: dict[str, int] = {name: 0 for name, _ in self._providers}
        self._last_metadata: dict[str, PaperMetadata] = {}

    @property
    def timeout_counts(self) -> dict[str, int]:
        """Cumulative per-provider timeout counts since this client was created."""
        return dict(self._timeout_counts)

    async def _fetch_one(self, name: str, provider: PaperDiscoveryClient, query: str, max_results: int) -> list[str]:
        # Circuit breaker: skip provider if it has too many consecutive timeouts
        if self._consecutive_timeouts.get(name, 0) >= _CIRCUIT_BREAKER_THRESHOLD:
            log.warning(
                "provider.circuit_open provider={} consecutive_timeouts={} — skipping",
                name, self._consecutive_timeouts[name],
            )
            return []

        try:
            if self._provider_timeout_seconds > 0:
                ids = await asyncio.wait_for(
                    provider.discover_arxiv_ids(query, max_results),
                    timeout=self._provider_timeout_seconds,
                )
            else:
                ids = await provider.discover_arxiv_ids(query, max_results)
        except TimeoutError:
            self._timeout_counts[name] = self._timeout_counts.get(name, 0) + 1
            streak = self._consecutive_timeouts.get(name, 0) + 1
            self._consecutive_timeouts[name] = streak
            log.warning(
                "provider.timeout provider={} timeout_s={:.1f} streak={} total_timeouts={}",
                name, self._provider_timeout_seconds, streak, self._timeout_counts[name],
            )
            if streak >= _DEGRADED_THRESHOLD:
                log.warning(
                    "provider.degraded provider={} consecutive_timeouts={} total_timeouts={} — consider checking API health",
                    name, streak, self._timeout_counts[name],
                )
            return []
        except DiscoveryAccessError as exc:
            log.warning("provider.failed provider={} error_type={} error_repr={}", name, type(exc).__name__, repr(exc))
            return []

        if not ids:
            log.info("provider.empty provider={} query={}", name, query)
            self._consecutive_timeouts[name] = 0  # reset streak on successful (empty) response
            return []

        self._consecutive_timeouts[name] = 0  # reset streak on success
        normalized = [normalize_arxiv_id(x) for x in ids]
        return [x for x in normalized if x]

    async def discover_arxiv_ids(self, query: str, max_results: int, *, is_raw_query: bool = True) -> list[str]:
        if max_results <= 0:
            return []

        # Create tasks for all providers and run in parallel
        # Raw-only providers (e.g. semantic_scholar) are skipped for reformulated query variants
        tasks: list[asyncio.Task] = [
            asyncio.create_task(self._fetch_one(name, provider, query, max_results))
            for name, provider in self._providers
            if is_raw_query or name not in self._raw_only_providers
        ]

        # Map tasks back to provider names for logging
        active_providers = [
            (name, provider) for name, provider in self._providers
            if is_raw_query or name not in self._raw_only_providers
        ]
        task_to_provider: dict[asyncio.Task, str] = {
            task: name for task, (name, _) in zip(tasks, active_providers)
        }

        # Wait for ALL tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        merged: list[str] = []
        merged_metadata: dict[str, PaperMetadata] = {}

        # Build map from task → provider instance for metadata collection
        task_to_provider_instance: dict[asyncio.Task, PaperDiscoveryClient] = {
            task: provider for task, (_, provider) in zip(tasks, active_providers)
        }

        # Process results in provider order
        for task, ids in zip(tasks, results):
            name = task_to_provider[task]

            # Handle exceptions from gather
            if isinstance(ids, Exception):
                log.warning("provider.exception provider={} error={}", name, type(ids).__name__)
                continue

            if not ids:
                log.info("provider.empty provider={} query={}", name, query)
                continue

            # Collect any metadata the provider cached alongside its IDs
            provider_instance = task_to_provider_instance[task]
            provider_meta: dict[str, PaperMetadata] = getattr(provider_instance, "_last_metadata", {})
            for k, v in provider_meta.items():
                merged_metadata.setdefault(k, v)

            # Merge results with dedup
            accepted = 0
            for arxiv_id in ids:
                if arxiv_id in seen:
                    continue
                seen.add(arxiv_id)
                merged.append(arxiv_id)
                accepted += 1

            if accepted:
                log.info("provider.success provider={} accepted={} merged={}", name, accepted, merged)

        self._last_metadata = merged_metadata

        if merged:
            return merged

        raise DiscoveryAccessError("No discovery provider returned arXiv IDs.")
