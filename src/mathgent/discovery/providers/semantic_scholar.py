"""Semantic Scholar discovery adapter — extracts arXiv IDs from paper search results.

Without a key the unauthenticated limit (~100 req/5 min shared) is hit instantly
in any concurrent workload, so the client skips gracefully when no key is present.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ...observability import get_logger, trace_span
from ..base import DiscoveryAccessError, PaperDiscoveryClient
from ..arxiv.ids import dedupe_preserve, normalize_arxiv_id

log = get_logger("discovery.semantic_scholar")

_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# Conservative: 1 request per second per client instance even with a key.
_MIN_INTERVAL_S = 1.1


class SemanticScholarDiscoveryClient(PaperDiscoveryClient):
    """Paper search via Semantic Scholar API, returning arXiv IDs.

    Uses the /paper/search endpoint with field filtering to only surface
    papers that have an arXiv ID. Falls back gracefully on 429 with
    exponential backoff.
    """

    _MAX_RETRIES = 3

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        """Ensure minimum interval between requests."""
        async with self._lock:
            now = time.monotonic()
            wait = _MIN_INTERVAL_S - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.semantic_scholar", query=query, max_results=max_results):
            await self._throttle()

            params = {
                "query": query,
                "fields": "externalIds,title",
                "limit": min(max_results * 2, 100),  # fetch extra since we filter to arXiv only
            }
            headers = {"x-api-key": self._api_key} if self._api_key else {}

            last_exc: Exception | None = None
            for attempt in range(self._MAX_RETRIES):
                try:
                    async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                        response = await client.get(_SEARCH_URL, params=params, headers=headers)

                    if response.status_code == 429:
                        retry_after = float(response.headers.get("retry-after", 2 ** (attempt + 1)))
                        log.warning(
                            "semantic_scholar.rate_limited attempt={} retry_after={:.1f}s",
                            attempt,
                            retry_after,
                        )
                        await asyncio.sleep(min(retry_after, 30.0))
                        continue

                    response.raise_for_status()
                    data = response.json()
                    break

                except httpx.TimeoutException as exc:
                    last_exc = exc
                    log.warning("semantic_scholar.timeout attempt={}", attempt)
                    await asyncio.sleep(2 ** attempt)
                    continue
                except httpx.HTTPStatusError as exc:
                    raise DiscoveryAccessError(f"Semantic Scholar HTTP {exc.response.status_code}") from exc
            else:
                raise DiscoveryAccessError(
                    f"Semantic Scholar failed after {self._MAX_RETRIES} attempts: {last_exc}"
                )

            ids: list[str] = []
            for paper in data.get("data", []):
                ext = paper.get("externalIds") or {}
                arxiv_raw = ext.get("ArXiv")
                if arxiv_raw:
                    ids.append(normalize_arxiv_id(str(arxiv_raw)))

            ids = dedupe_preserve(ids, max_results=max_results)
            log.info("done count={} ids={}", len(ids), ids)
            return ids
