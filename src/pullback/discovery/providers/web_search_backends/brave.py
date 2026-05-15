from __future__ import annotations

import os
from typing import Any

import httpx

from ....observability import get_logger

log = get_logger("discovery.web_search.brave")


class BraveWebSearchBackend:
    """Brave Search API backend.

    Requires `BRAVE_SEARCH_API_KEY` in the environment.
    Returns URL + optional title so callers can do lightweight heuristics.
    """

    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = 10.0) -> None:
        self._api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        self._timeout_seconds = timeout_seconds

    async def search(self, *, query: str, max_results: int):
        if not self._api_key:
            raise RuntimeError("BRAVE_SEARCH_API_KEY is required for BraveWebSearchBackend")
        if max_results <= 0:
            return []

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"X-Subscription-Token": self._api_key}
        params = {"q": query, "count": str(min(max_results, 20))}

        timeout = httpx.Timeout(self._timeout_seconds) if self._timeout_seconds > 0 else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            payload: Any = resp.json()

        # Import here to avoid circular imports during type checking.
        from ..web_search_arxiv import WebSearchResult

        results_out: list[WebSearchResult] = []
        web = payload.get("web") if isinstance(payload, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    u = item.get("url")
                    if not isinstance(u, str) or not u:
                        continue
                    title = item.get("title")
                    title_str = title if isinstance(title, str) and title.strip() else None
                    results_out.append(WebSearchResult(url=u, title=title_str))
        log.info("brave.done query={!r} results={}", query, len(results_out))
        return results_out
