import asyncio

from mathgent.agents import ForagerAgent
from mathgent.models import LemmaHeader


class _FakeTools:
    def __init__(self) -> None:
        self.headers = [
            LemmaHeader(line_number=10, line="\\begin{lemma} Unrelated statement"),
            LemmaHeader(line_number=20, line="\\begin{theorem} Banach fixed point theorem"),
        ]
        self.snippets = {
            10: "\\begin{lemma} Banach fixed point theorem for non-reflexive spaces\\end{lemma}",
            20: "\\begin{theorem} Banach fixed point theorem for non-reflexive spaces\\end{theorem}",
        }

    async def get_paper_headers(self, arxiv_id: str) -> list[LemmaHeader]:
        _ = arxiv_id
        return self.headers

    async def fetch_latex_block(
        self,
        arxiv_id: str,
        line_number: int,
        context_lines: int = 20,
        environment_name: str | None = None,
    ) -> str:
        _ = arxiv_id, context_lines, environment_name
        return self.snippets[line_number]

    async def fetch_header_block(
        self,
        arxiv_id: str,
        line_number: int,
        header_line: str,
        *,
        context_lines: int = 20,
    ) -> str:
        _ = header_line
        return await self.fetch_latex_block(arxiv_id, line_number, context_lines=context_lines)


class _SelectFirst:
    async def select_line(
        self,
        *,
        query: str,
        arxiv_id: str,
        headers: list[LemmaHeader],
        heuristic_line: int,
    ) -> int | None:
        _ = query, arxiv_id, heuristic_line
        return headers[0].line_number


def test_forager_uses_heuristic_header_selection_by_default() -> None:
    tools = _FakeTools()
    agent = ForagerAgent(tools=tools)

    result = asyncio.run(
        agent.forage(
            query="Banach fixed point theorem",
            arxiv_id="2401.00001",
            strictness=0.1,
        )
    )
    assert result is not None
    assert result.line_number == 20


def test_forager_supports_pluggable_header_selector() -> None:
    tools = _FakeTools()
    agent = ForagerAgent(tools=tools, header_selector=_SelectFirst())

    result = asyncio.run(
        agent.forage(
            query="Banach fixed point theorem",
            arxiv_id="2401.00001",
            strictness=0.1,
        )
    )
    assert result is not None
    assert result.line_number == 10
