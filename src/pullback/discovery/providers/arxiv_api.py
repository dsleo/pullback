"""arXiv export API discovery provider."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import httpx

from ...observability import get_logger, trace_span
from ..arxiv.ids import dedupe_preserve, extract_arxiv_id_from_text
from ..base import DiscoveryAccessError, PaperDiscoveryClient

log = get_logger("discovery.arxiv_api")
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}


class ArxivAPIDiscoveryClient(PaperDiscoveryClient):
    """Lightweight arXiv API provider (no API key required)."""

    _SORT_MAP = {
        "relevance": "relevance",
        "date": "submittedDate",
        "updated": "lastUpdatedDate",
    }

    def __init__(
        self,
        *,
        timeout_seconds: float = 12.0,
        sort: str = "relevance",
        user_agent: str = "pullback/0.1",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._sort = sort
        self._user_agent = user_agent

    def _build_params(self, *, query: str, max_results: int) -> dict[str, str]:
        sort_by = self._SORT_MAP.get(self._sort, "relevance")
        return {
            "search_query": f"all:{query}",
            "max_results": str(max_results),
            "sortBy": sort_by,
            "sortOrder": "descending",
        }

    async def discover_arxiv_ids(self, query: str, max_results: int) -> list[str]:
        with trace_span("discovery.arxiv_api", query=query, max_results=max_results):
            cleaned = query.strip()
            if not cleaned or max_results <= 0:
                return []

            direct_id = extract_arxiv_id_from_text(cleaned, allow_bare=True)
            if direct_id:
                return [direct_id]

            params = self._build_params(query=cleaned, max_results=max_results)
            headers = {"User-Agent": self._user_agent}

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                try:
                    if self._timeout_seconds > 0:
                        response = await asyncio.wait_for(
                            client.get(ARXIV_API_URL, params=params, headers=headers),
                            timeout=self._timeout_seconds,
                        )
                    else:
                        response = await client.get(ARXIV_API_URL, params=params, headers=headers)
                except TimeoutError as exc:
                    raise DiscoveryAccessError(
                        f"arXiv API request timed out after {self._timeout_seconds:.1f}s"
                    ) from exc
                except httpx.RequestError as exc:
                    raise DiscoveryAccessError("arXiv API request failed") from exc

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                preview = response.text[:240].replace("\n", " ")
                raise DiscoveryAccessError(
                    f"arXiv API request failed (status {response.status_code}). body={preview}"
                ) from exc

            try:
                root = ET.fromstring(response.text)
            except ET.ParseError as exc:
                raise DiscoveryAccessError("arXiv API returned invalid XML") from exc

            ids: list[str] = []
            for entry in root.findall("a:entry", ARXIV_NS):
                raw_id = entry.findtext("a:id", default="", namespaces=ARXIV_NS).strip()
                if not raw_id:
                    continue
                arxiv_id = extract_arxiv_id_from_text(raw_id, allow_bare=True)
                if arxiv_id:
                    ids.append(arxiv_id)

            ids = dedupe_preserve(ids, max_results=max_results)
            log.info("done count={} ids={}", len(ids), ids)
            return ids
