import asyncio

from mathgent.sandbox import LocalSandboxRunner
from mathgent.tools import ExtractionTools

DUMMY_TEX = r"""
\\section{Intro}
Some text.
\\begin{lemma}[Banach fixed-point]
Let (X, d) be complete.
Then T has a unique fixed point.
\\end{lemma}

More text.
\\begin{theorem}[Background theorem]
This theorem should not be extracted.
\\end{theorem}

\\begin{proposition}[Non-reflexive extension]
Assume X is non-reflexive.
Then a weak-star version holds.
\\end{proposition}
""".strip()


def test_get_paper_headers_and_fetch_latex_block(tmp_path) -> None:
    arxiv_id = "2401.00001"
    tex_file = tmp_path / f"{arxiv_id}.tex"
    tex_file.write_text(DUMMY_TEX)

    runner = LocalSandboxRunner({arxiv_id: tex_file})
    tools = ExtractionTools(runner)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    assert [h.line_number for h in headers] == [3, 9, 13]
    assert "Banach fixed-point" in headers[0].line

    block = asyncio.run(
        tools.fetch_latex_block(
            arxiv_id=arxiv_id,
            line_number=13,
            context_lines=2,
            environment_name="proposition",
        )
    )
    assert "\\begin{proposition}[Non-reflexive extension]" in block
    assert "weak-star" in block
    assert "\\section{Intro}" not in block
    assert "\\begin{theorem}[Background theorem]" not in block
