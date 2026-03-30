import asyncio

from mathgent.tools import ExtractionTools


class FakeRunner:
    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.commands: list[str] = []

    async def resolve_paper_path(self, arxiv_id: str) -> str:
        self.resolved.append(arxiv_id)
        return "/tmp/paper.tex"

    async def run_shell(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("python - <<'PY'"):
            return r"12:\\begin{lemma}[Banach]"
        if command.startswith("wc -l"):
            return "120\n"
        if command.startswith("sed"):
            return "lemma body"
        return ""


def test_forager_tools_resolve_paper_path_first() -> None:
    runner = FakeRunner()
    tools = ExtractionTools(runner)

    headers = asyncio.run(tools.get_paper_headers("2401.00001"))
    snippet = asyncio.run(tools.fetch_latex_block("2401.00001", line_number=12, context_lines=20))

    assert [h.line_number for h in headers] == [12]
    assert snippet == "lemma body"
    assert runner.resolved == ["2401.00001", "2401.00001"]
