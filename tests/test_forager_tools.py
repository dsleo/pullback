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

DYNAMIC_ENV_TEX = r"""
\\documentclass{article}
\\newtheorem{mainthm}{Main Theorem}
\\newtheorem*{keylem}{Key Lemma}

\\begin{document}
\\begin{mainthm}\\label{thm:main}
Main theorem statement.
\\end{mainthm}

\\begin{keylem}
Key lemma statement.
\\end{keylem}
\\end{document}
""".strip()

NUMBERED_LABEL_TEX = r"""
\section{Main}
\begin{theorem}\label{thm:3.1}
Statement.
\end{theorem}
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


def test_get_paper_headers_handles_dynamic_environments(tmp_path) -> None:
    arxiv_id = "2401.00002"
    tex_file = tmp_path / f"{arxiv_id}.tex"
    tex_file.write_text(DYNAMIC_ENV_TEX)

    runner = LocalSandboxRunner({arxiv_id: tex_file})
    tools = ExtractionTools(runner)

    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    lines = [header.line for header in headers]
    assert any("\\begin{mainthm}" in line for line in lines)
    assert any("\\begin{keylem}" in line for line in lines)

    selected = next(header for header in headers if "\\begin{mainthm}" in header.line)
    block = asyncio.run(
        tools.fetch_header_block(
            arxiv_id=arxiv_id,
            line_number=selected.line_number,
            header_line=selected.line,
            context_lines=2,
        )
    )
    assert "\\begin{mainthm}" in block
    assert "Main theorem statement." in block
    assert "\\begin{keylem}" not in block


def test_fetch_latex_block_returns_environment_without_synthetic_number_guess(tmp_path) -> None:
    arxiv_id = "2401.00003"
    tex_file = tmp_path / f"{arxiv_id}.tex"
    tex_file.write_text(NUMBERED_LABEL_TEX)

    runner = LocalSandboxRunner({arxiv_id: tex_file})
    tools = ExtractionTools(runner)
    headers = asyncio.run(tools.get_paper_headers(arxiv_id))
    assert headers

    block = asyncio.run(
        tools.fetch_header_block(
            arxiv_id=arxiv_id,
            line_number=headers[0].line_number,
            header_line=headers[0].line,
            context_lines=2,
        )
    )
    assert "% theorem_number_guess:" not in block
    assert "\\begin{theorem}" in block
    assert "\\label{thm:3.1}" in block
