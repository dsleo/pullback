"""Forager execution: pick a theorem-like header, extract a block, rerank, and threshold."""

from __future__ import annotations

from ..models import LemmaMatch
from ..observability import get_logger, logfire_info, trace_span
from ..rerank import Reranker, TokenOverlapReranker
from ..tools import ExtractionTools
from .header_selection import HeaderSelector, HeuristicHeaderSelector

log = get_logger("forager")


class ForagerAgent:
    def __init__(
        self,
        *,
        reranker: Reranker | None = None,
        tools: ExtractionTools | None = None,
        header_selector: HeaderSelector | None = None,
    ) -> None:
        self._reranker = reranker or TokenOverlapReranker()
        self._tools = tools
        self._header_selector = header_selector or HeuristicHeaderSelector()

    def set_tools(self, tools: ExtractionTools) -> None:
        self._tools = tools

    async def forage(self, query: str, arxiv_id: str, strictness: float) -> LemmaMatch | None:
        if self._tools is None:
            raise RuntimeError("Forager tools are not configured.")

        with trace_span("forager.forage", query=query, arxiv_id=arxiv_id, strictness=strictness):
            tools = self._tools
            log.info("forage.start arxiv_id={} strictness={}", arxiv_id, strictness)
            headers = await tools.get_paper_headers(arxiv_id)
            if not headers:
                log.info("forage.no_headers arxiv_id={}", arxiv_id)
                return None

            scored: list[tuple[float, int]] = []
            for header in headers:
                score = self._reranker.score(query, header.line)
                scored.append((score, header.line_number))
            scored.sort(key=lambda item: item[0], reverse=True)
            heuristic_line = scored[0][1]

            selected_line = await self._header_selector.select_line(
                query=query,
                arxiv_id=arxiv_id,
                headers=headers,
                heuristic_line=heuristic_line,
            )
            header_map = {h.line_number: h for h in headers}
            selected_header = header_map.get(selected_line, header_map[heuristic_line])

            log.info(
                "forage.header_selected arxiv_id={} selected_line={} heuristic_line={}",
                arxiv_id,
                selected_line,
                heuristic_line,
            )

            snippet = await tools.fetch_header_block(
                arxiv_id,
                selected_header.line_number,
                selected_header.line,
                context_lines=20,
            )
            snippet_score = self._reranker.score(query, snippet)
            log.info("forage.scored arxiv_id={} score={:.4f}", arxiv_id, snippet_score)
            logfire_info("forager scored snippet", arxiv_id=arxiv_id, score=snippet_score)
            if snippet_score < strictness:
                log.info("forage.below_strictness arxiv_id={} strictness={}", arxiv_id, strictness)
                return None

            return LemmaMatch(
                arxiv_id=arxiv_id,
                line_number=selected_header.line_number,
                header_line=selected_header.line,
                snippet=snippet,
                score=snippet_score,
            )
