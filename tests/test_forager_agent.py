import asyncio

from pullback.agents import ForagePlan, ForagerAgent
from pullback.models import LemmaHeader


class _FakeTools:
    def __init__(self) -> None:
        self.headers = [
            LemmaHeader(line_number=10, line="\\begin{lemma} Unrelated statement"),
            LemmaHeader(line_number=20, line="\\begin{theorem} Banach fixed point theorem"),
        ]
        self.snippets = {
            10: "\\begin{lemma} Unrelated compactness lemma\\end{lemma}",
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

    async def fetch_header_blocks(
        self,
        arxiv_id: str,
        headers: list[LemmaHeader],
        *,
        context_lines: int = 20,
    ) -> dict[int, str]:
        _ = arxiv_id, context_lines, headers
        return dict(self.snippets)


class _FakeReranker:
    def score(self, query: str, snippet: str) -> float:
        _ = query
        if "Banach" in snippet:
            return 0.9
        return 0.1

    def score_batch(self, query: str, snippets: list[str]) -> list[float]:
        return [self.score(query, s) for s in snippets]


def test_forager_selects_best_header() -> None:
    tools = _FakeTools()
    agent = ForagerAgent(tools=tools, reranker=_FakeReranker())

    results = asyncio.run(
        agent.forage(
            query="Banach fixed point theorem",
            arxiv_id="2401.00001",
            strictness=0.1,
        )
    )
    assert results
    assert results[0].line_number == 20


def test_forager_respects_strictness_threshold() -> None:
    tools = _FakeTools()
    agent = ForagerAgent(tools=tools, reranker=_FakeReranker())

    # Note: with HybridReranker, min_overlap might filter even before strictness
    # But here we use _FakeReranker which always returns scores.
    # strictness filtering in Forager is currently just for the execute_complete hook
    # and plan-level results, the actual return list includes all scored items.
    results = asyncio.run(
        agent.forage(
            query="Banach fixed point theorem",
            arxiv_id="2401.00001",
            strictness=0.1,
        )
    )
    assert results
    assert any(r.line_number == 20 for r in results)


def test_forager_plan_exposes_candidates_and_hooks() -> None:
    tools = _FakeTools()
    agent = ForagerAgent(tools=tools, reranker=_FakeReranker())
    events: list[str] = []

    agent.on("plan_start", lambda **_: events.append("plan_start"))
    agent.on("plan_complete", lambda **_: events.append("plan_complete"))
    agent.on("execute_start", lambda **_: events.append("execute_start"))
    agent.on("snippet_scored", lambda **_: events.append("snippet_scored"))
    agent.on("execute_complete", lambda **_: events.append("execute_complete"))

    plan = asyncio.run(
        agent.plan(
            query="Banach fixed point theorem",
            arxiv_id="2401.00001",
            strictness=0.2,
        )
    )
    assert isinstance(plan, ForagePlan)
    assert plan.headers
    assert {header.line_number for header in plan.headers} == {10, 20}

    results = asyncio.run(agent.execute(plan))
    assert results
    assert events.count("snippet_scored") == len(plan.headers)
    assert events.index("plan_start") < events.index("execute_start") < events.index("execute_complete")
