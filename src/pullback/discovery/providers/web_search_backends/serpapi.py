from __future__ import annotations

import os
from typing import Any

import httpx

from ....observability import get_logger

log = get_logger("discovery.web_search.serpapi")


class SerpApiWebSearchBackend:
    """SerpApi backend (Google engine by default).

    Requires `SERP_API_KEY` in the environment.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 12.0,
        engine: str = "google",
    ) -> None:
        self._api_key = api_key or os.getenv("SERP_API_KEY")
        self._timeout_seconds = timeout_seconds
        self._engine = engine

    async def search(self, *, query: str, max_results: int):
        if not self._api_key:
            raise RuntimeError("SERP_API_KEY is required for SerpApiWebSearchBackend")
        if max_results <= 0:
            return []

        url = "https://serpapi.com/search.json"
        params = {
            "engine": self._engine,
            "q": query,
            "api_key": self._api_key,
            # SerpApi uses `num` for number of results (Google engine).
            "num": str(min(max_results, 20)),
        }

        timeout = httpx.Timeout(self._timeout_seconds) if self._timeout_seconds > 0 else None
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload: Any = resp.json()

        from ..web_search_arxiv import WebSearchResult

        results_out: list[WebSearchResult] = []
        organic = payload.get("organic_results") if isinstance(payload, dict) else None
        if isinstance(organic, list):
            for item in organic:
                if not isinstance(item, dict):
                    continue
                link = item.get("link")
                if not isinstance(link, str) or not link:
                    continue
                title = item.get("title")
                title_str = title if isinstance(title, str) and title.strip() else None
                results_out.append(WebSearchResult(url=link, title=title_str))

        log.info("serpapi.done query={!r} results={}", query, len(results_out))
        return results_out

